"""FastAPI routes kept separate from Text2SQL business logic."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from opentelemetry.trace import SpanKind
from pydantic import BaseModel

from sherlock.api.chat_models import (
    ChatRequest,
    ChatResponse,
    ChatStateErrorResponse,
    ChatStreamError,
)
from sherlock.api.schemas import (
    BacktestResponse,
    QueryRequest,
    QueryResponse,
    RuleComparisonRequest,
    RuleComparisonResponse,
    RuleGenerateRequest,
    RuleGenerateResponse,
    RuleRefineRequest,
    RuleRefineResponse,
    RuleRequest,
)
from sherlock.chat import (
    ChatAgentError,
    ChatAgentFactory,
    InvalidChatState,
    MissingChatState,
)
from sherlock.execution import (
    WorkflowController,
    WorkflowDeadlineExceeded,
    WorkflowKind,
    WorkflowLease,
    WorkflowOverloaded,
)
from sherlock.services.backtest import (
    BacktestError,
    BacktestService,
    InvalidBacktestRule,
)
from sherlock.services.rule_comparison import (
    InvalidComparisonRule,
    RuleComparisonService,
)
from sherlock.services.rule_generation import (
    InvalidCurrentRule,
    RuleGenerationError,
    RuleGenerationService,
)
from sherlock.services.rule_validation import RuleSchemaError
from sherlock.services.text2sql import (
    QueryExecutionError,
    Text2SQLError,
    Text2SQLService,
)
from sherlock.telemetry import CHAT_TURN_SPAN, SpanOutcome, span

router = APIRouter(prefix="/v1")
MAX_STREAM_BUFFERED_EVENTS = 8
MAX_STREAM_BUFFERED_BYTES = 256_000


class StreamBufferFull(RuntimeError):
    """The SSE writer cannot safely retain more producer output."""


class StreamBuffer:
    """A bounded producer-to-writer buffer with an out-of-band terminal event."""

    def __init__(self) -> None:
        self._events: asyncio.Queue[str] = asyncio.Queue(MAX_STREAM_BUFFERED_EVENTS)
        self._buffered_bytes = 0
        self._closed = asyncio.Event()
        self.terminal_event: str | None = None

    async def put(self, event: str) -> None:
        """Append an event after giving a runnable writer one chance to drain."""

        event_bytes = len(event.encode("utf-8"))
        if self._buffered_bytes + event_bytes > MAX_STREAM_BUFFERED_BYTES:
            raise StreamBufferFull
        if self._events.full():
            # A producer can synchronously emit several chunks before the response
            # task has run. Yield once before treating a full queue as backpressure.
            await asyncio.sleep(0)
            if self._events.full():
                raise StreamBufferFull
        self._events.put_nowait(event)
        self._buffered_bytes += event_bytes

    def close(self, terminal_event: str | None = None) -> None:
        if terminal_event is not None:
            self.terminal_event = terminal_event
        self._closed.set()

    async def get(self) -> str | None:
        while True:
            try:
                return self._take_nowait()
            except asyncio.QueueEmpty:
                if self._closed.is_set():
                    return None
            event_wait = asyncio.create_task(self._events.get())
            close_wait = asyncio.create_task(self._closed.wait())
            try:
                done, _ = await asyncio.wait(
                    (event_wait, close_wait), return_when=asyncio.FIRST_COMPLETED
                )
                if event_wait in done:
                    event = event_wait.result()
                    self._buffered_bytes -= len(event.encode("utf-8"))
                    return event
            finally:
                for task in (event_wait, close_wait):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(event_wait, close_wait, return_exceptions=True)

    def _take_nowait(self) -> str:
        event = self._events.get_nowait()
        self._buffered_bytes -= len(event.encode("utf-8"))
        return event


def _media_range_quality(parameters: list[str]) -> float:
    quality = next(
        (
            value
            for name, _, value in (parameter.partition("=") for parameter in parameters)
            if name == "q"
        ),
        "1",
    )
    try:
        return float(quality)
    except ValueError:
        return 0.0


def _accepts_event_stream(accept: str) -> bool:
    media_ranges = [
        [part.strip() for part in media_range.lower().split(";")]
        for media_range in accept.split(",")
    ]
    return any(
        media_type == "text/event-stream" and _media_range_quality(parameters) > 0
        for media_type, *parameters in media_ranges
    )


def get_text2sql_service(request: Request) -> Text2SQLService:
    """Return the application-scoped Text2SQL service."""

    return request.app.state.text2sql_service


Text2SQLDependency = Annotated[Text2SQLService, Depends(get_text2sql_service)]


def get_rule_generation_service(request: Request) -> RuleGenerationService:
    """Return the application-scoped candidate-rule service."""

    return request.app.state.rule_generation_service


RuleGenerationDependency = Annotated[
    RuleGenerationService, Depends(get_rule_generation_service)
]


def get_backtest_service(request: Request) -> BacktestService:
    return request.app.state.backtest_service


BacktestDependency = Annotated[BacktestService, Depends(get_backtest_service)]


def get_rule_comparison_service(request: Request) -> RuleComparisonService:
    return request.app.state.rule_comparison_service


RuleComparisonDependency = Annotated[
    RuleComparisonService, Depends(get_rule_comparison_service)
]


def get_chat_agent_factory(request: Request) -> ChatAgentFactory:
    return request.app.state.chat_agent_factory


ChatAgentFactoryDependency = Annotated[
    ChatAgentFactory, Depends(get_chat_agent_factory)
]


def get_workflow_controller(request: Request) -> WorkflowController:
    """Return the application-scoped per-worker workflow admission controller."""

    return request.app.state.workflow_controller


WorkflowControllerDependency = Annotated[
    WorkflowController, Depends(get_workflow_controller)
]


def _workflow_error(
    exception: WorkflowOverloaded | WorkflowDeadlineExceeded,
) -> HTTPException:
    if isinstance(exception, WorkflowOverloaded):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sherlock is busy. Please try again shortly.",
            headers={"Retry-After": "1"},
        )
    return HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail="Sherlock could not complete the request in time. Please try again.",
    )


@router.get("/health")
async def health() -> dict[str, str]:
    """Report API readiness without invoking analytical dependencies."""

    return {"status": "ok"}


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    service: Text2SQLDependency,
    controller: WorkflowControllerDependency,
) -> QueryResponse:
    """Translate a natural-language question to SQL and execute it."""

    try:
        async with controller.workflow(WorkflowKind.QUERY):
            result = await service.query(request.question)
    except (WorkflowOverloaded, WorkflowDeadlineExceeded) as exc:
        raise _workflow_error(exc) from exc
    except Text2SQLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return QueryResponse.model_validate(result)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    factory: ChatAgentFactoryDependency,
    controller: WorkflowControllerDependency,
) -> ChatResponse | StreamingResponse:
    """Route one stateless conversational turn through a fresh ChatAgent."""

    agent = factory.create(request.history, request.working_state)
    if _accepts_event_stream(http_request.headers.get("accept", "")):
        try:
            lease = await controller.admit(WorkflowKind.CHAT)
        except WorkflowOverloaded as exc:
            raise _workflow_error(exc) from exc
        return StreamingResponse(
            _traced_chat_event_stream(
                agent.stream(request.message), len(request.history), lease
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        async with (
            span(
                CHAT_TURN_SPAN,
                attributes={"sherlock.chat.history_messages": len(request.history)},
                kind=SpanKind.SERVER,
            ),
            span("sherlock.chat.agent"),
            controller.workflow(WorkflowKind.CHAT),
        ):
            return await agent.respond(request.message)
    except (WorkflowOverloaded, WorkflowDeadlineExceeded) as exc:
        raise _workflow_error(exc) from exc
    except MissingChatState as exc:
        detail = ChatStateErrorResponse(
            code="MISSING_WORKING_STATE",
            message=str(exc),
            intent=exc.intent,
            missing_fields=exc.missing_fields,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail.model_dump(mode="json"),
        ) from exc
    except InvalidChatState as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_WORKING_STATE",
                "message": str(exc),
                "intent": exc.intent,
                **exc.detail,
            },
        ) from exc
    except ChatAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


def _sse(event: str, payload: BaseModel) -> str:
    data = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: {event}\ndata: {data}\n\n"


def _record_stream_failure(
    outcome: SpanOutcome | None,
    mark_failed: Callable[[], None] | None,
    exception: BaseException,
) -> None:
    if outcome is not None:
        outcome.fail(exception)
    if mark_failed is not None:
        mark_failed()


async def _close_async_iterator[T](iterator: AsyncIterator[T]) -> None:
    """Close an async-generator stream before relinquishing its workflow lease."""

    await cast(AsyncGenerator[T], iterator).aclose()


async def _chat_event_stream(
    events: AsyncIterator[tuple[str, BaseModel]],
    outcome: SpanOutcome | None = None,
    mark_failed: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    try:
        async for event, payload in events:
            yield _sse(event, payload)
    except MissingChatState as exc:
        _record_stream_failure(outcome, mark_failed, exc)
        if "candidate_rule" in exc.missing_fields:
            message = "Create or select a candidate rule before running this activity."
        else:
            message = (
                "A previous candidate rule is needed before Sherlock can compare rules."
            )
        yield _sse("error", ChatStreamError(message=message))
    except InvalidChatState as exc:
        _record_stream_failure(outcome, mark_failed, exc)
        yield _sse(
            "error",
            ChatStreamError(
                message=(
                    "The saved candidate rule is no longer valid. "
                    "Review or replace it before continuing."
                )
            ),
        )
    except ChatAgentError as exc:
        _record_stream_failure(outcome, mark_failed, exc)
        yield _sse(
            "error",
            ChatStreamError(
                message="Sherlock could not complete the request. Please try again."
            ),
        )
    finally:
        await _close_async_iterator(events)


async def _traced_chat_event_stream(
    events: AsyncIterator[tuple[str, BaseModel]],
    history_messages: int,
    lease: WorkflowLease,
) -> AsyncIterator[str]:
    """Write completed producer events without putting response writes on its clock."""

    buffer = StreamBuffer()
    producer = asyncio.create_task(
        _produce_chat_events(events, history_messages, lease, buffer)
    )
    try:
        while (event := await buffer.get()) is not None:
            yield event
        if buffer.terminal_event is not None:
            yield buffer.terminal_event
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


async def _produce_chat_events(
    events: AsyncIterator[tuple[str, BaseModel]],
    history_messages: int,
    lease: WorkflowLease,
    buffer: StreamBuffer,
) -> None:
    """Run the deadline-bound workflow independently from ASGI response writes."""

    outcome = SpanOutcome()
    transformed_events = _chat_event_stream(events, outcome, lease.mark_failed)
    try:
        async with (
            span(
                CHAT_TURN_SPAN,
                attributes={"sherlock.chat.history_messages": history_messages},
                kind=SpanKind.SERVER,
                outcome=outcome,
            ),
            span("sherlock.chat.agent", outcome=outcome),
        ):
            try:
                async with lease:
                    try:
                        async for event in transformed_events:
                            await buffer.put(event)
                    finally:
                        await _close_async_iterator(transformed_events)
            except StreamBufferFull as exc:
                lease.mark_failed()
                outcome.fail(exc)
                buffer.close(
                    _sse(
                        "error",
                        ChatStreamError(
                            message="Sherlock could not complete the request. Please try again."
                        ),
                    )
                )
            except WorkflowDeadlineExceeded as exc:
                outcome.fail(exc)
                buffer.close(
                    _sse(
                        "error",
                        ChatStreamError(
                            message="Sherlock could not complete the request in time. Please try again."
                        ),
                    )
                )
    finally:
        buffer.close()


@router.post("/rules/generate", response_model=RuleGenerateResponse)
async def generate_rule(
    request: RuleGenerateRequest,
    service: RuleGenerationDependency,
    controller: WorkflowControllerDependency,
) -> RuleGenerateResponse:
    """Generate and deterministically validate a candidate fraud rule."""

    try:
        async with controller.workflow(WorkflowKind.RULE_GENERATION):
            result = await service.generate(request.instruction)
    except (WorkflowOverloaded, WorkflowDeadlineExceeded) as exc:
        raise _workflow_error(exc) from exc
    except (RuleGenerationError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return RuleGenerateResponse.model_validate(result)


@router.post("/rules/refine", response_model=RuleRefineResponse)
async def refine_rule(
    request: RuleRefineRequest,
    service: RuleGenerationDependency,
    controller: WorkflowControllerDependency,
) -> RuleRefineResponse:
    """Apply a contextual modification to an explicit candidate rule."""

    try:
        async with controller.workflow(WorkflowKind.RULE_GENERATION):
            result = await service.refine(request.rule, request.instruction)
    except (WorkflowOverloaded, WorkflowDeadlineExceeded) as exc:
        raise _workflow_error(exc) from exc
    except InvalidCurrentRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.validation.as_dict(),
        ) from exc
    except (RuleGenerationError, RuleSchemaError, QueryExecutionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return RuleRefineResponse.model_validate(result)


@router.post("/rules/backtest", response_model=BacktestResponse)
async def backtest_rule(
    request: RuleRequest,
    service: BacktestDependency,
    controller: WorkflowControllerDependency,
) -> BacktestResponse:
    """Replay a candidate rule against historical transactions."""

    try:
        async with controller.workflow(WorkflowKind.BACKTEST):
            result = await service.backtest(request.rule)
    except (WorkflowOverloaded, WorkflowDeadlineExceeded) as exc:
        raise _workflow_error(exc) from exc
    except InvalidBacktestRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.validation.as_dict(),
        ) from exc
    except BacktestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return BacktestResponse.model_validate(result)


@router.post("/rules/compare", response_model=RuleComparisonResponse)
async def compare_rules(
    request: RuleComparisonRequest,
    service: RuleComparisonDependency,
    controller: WorkflowControllerDependency,
) -> RuleComparisonResponse:
    """Compare current and previous rules using deterministic backtests."""

    try:
        async with controller.workflow(WorkflowKind.COMPARISON):
            result = await service.compare(request.current_rule, request.previous_rule)
    except (WorkflowOverloaded, WorkflowDeadlineExceeded) as exc:
        raise _workflow_error(exc) from exc
    except InvalidComparisonRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"rule_role": exc.role, **exc.validation.as_dict()},
        ) from exc
    except BacktestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return RuleComparisonResponse.model_validate(result)
