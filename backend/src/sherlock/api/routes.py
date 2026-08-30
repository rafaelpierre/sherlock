"""FastAPI routes kept separate from Text2SQL business logic."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
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
from sherlock.services.text2sql import Text2SQLError, Text2SQLService

router = APIRouter(prefix="/v1")


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


@router.get("/health")
async def health() -> dict[str, str]:
    """Report API readiness without invoking analytical dependencies."""

    return {"status": "ok"}


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    service: Text2SQLDependency,
) -> QueryResponse:
    """Translate a natural-language question to SQL and execute it."""

    try:
        result = await service.query(request.question)
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
) -> ChatResponse | StreamingResponse:
    """Route one stateless conversational turn through a fresh ChatAgent."""

    agent = factory.create(request.history, request.working_state)
    if _accepts_event_stream(http_request.headers.get("accept", "")):
        return StreamingResponse(
            _chat_event_stream(agent.stream(request.message)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        return await agent.respond(request.message)
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


async def _chat_event_stream(
    events: AsyncIterator[tuple[str, BaseModel]],
) -> AsyncIterator[str]:
    try:
        async for event, payload in events:
            yield _sse(event, payload)
    except MissingChatState as exc:
        if "candidate_rule" in exc.missing_fields:
            message = "Create or select a candidate rule before running this activity."
        else:
            message = (
                "A previous candidate rule is needed before Sherlock can compare rules."
            )
        yield _sse("error", ChatStreamError(message=message))
    except InvalidChatState:
        yield _sse(
            "error",
            ChatStreamError(
                message=(
                    "The saved candidate rule is no longer valid. "
                    "Review or replace it before continuing."
                )
            ),
        )
    except ChatAgentError:
        yield _sse(
            "error",
            ChatStreamError(
                message="Sherlock could not complete the request. Please try again."
            ),
        )


@router.post("/rules/generate", response_model=RuleGenerateResponse)
async def generate_rule(
    request: RuleGenerateRequest,
    service: RuleGenerationDependency,
) -> RuleGenerateResponse:
    """Generate and deterministically validate a candidate fraud rule."""

    try:
        result = await service.generate(request.instruction)
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
) -> RuleRefineResponse:
    """Apply a contextual modification to an explicit candidate rule."""

    try:
        result = await service.refine(request.rule, request.instruction)
    except InvalidCurrentRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.validation.as_dict(),
        ) from exc
    except RuleGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return RuleRefineResponse.model_validate(result)


@router.post("/rules/backtest", response_model=BacktestResponse)
async def backtest_rule(
    request: RuleRequest,
    service: BacktestDependency,
) -> BacktestResponse:
    """Replay a candidate rule against historical transactions."""

    try:
        result = await service.backtest(request.rule)
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
) -> RuleComparisonResponse:
    """Compare current and previous rules using deterministic backtests."""

    try:
        result = await service.compare(request.current_rule, request.previous_rule)
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
