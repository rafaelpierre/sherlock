from __future__ import annotations

import asyncio
import logging
import threading
from typing import cast

import pytest
from fastapi import HTTPException

from sherlock.api.chat_models import ChatTextDelta
from sherlock.api.routes import (
    MAX_STREAM_BUFFERED_BYTES,
    MAX_STREAM_BUFFERED_EVENTS,
    StreamBuffer,
    StreamBufferFull,
    _traced_chat_event_stream,
    query,
)
from sherlock.api.schemas import QueryRequest
from sherlock.chat import ChatAgentError
from sherlock.execution import (
    ExecutionLimits,
    WorkflowController,
    WorkflowDeadlineExceeded,
    WorkflowKind,
    WorkflowOverloaded,
    run_blocking_provider_call,
)
from sherlock.services.text2sql import Text2SQLService


def test_deadline_covers_multiple_repair_steps() -> None:
    async def run() -> None:
        controller = WorkflowController(ExecutionLimits(deadline_seconds=0.01))
        with pytest.raises(WorkflowDeadlineExceeded):
            async with controller.workflow(WorkflowKind.QUERY):
                await asyncio.sleep(0.006)  # generation
                await asyncio.sleep(0.006)  # execution/repair exceeds turn budget

    asyncio.run(run())


def test_overload_rejects_without_waiting_and_releases_partial_permits() -> None:
    async def run() -> None:
        controller = WorkflowController(
            ExecutionLimits(model_in_flight_limit=1, mcp_in_flight_limit=1)
        )
        async with controller.workflow(WorkflowKind.QUERY):
            with pytest.raises(WorkflowOverloaded):
                await controller.admit(WorkflowKind.QUERY)
        async with controller.workflow(WorkflowKind.QUERY):
            pass

    asyncio.run(run())


def test_cancellation_releases_permits_for_the_next_workflow() -> None:
    async def run() -> None:
        controller = WorkflowController(
            ExecutionLimits(model_in_flight_limit=1, mcp_in_flight_limit=1)
        )
        entered = asyncio.Event()

        async def blocked() -> None:
            async with controller.workflow(WorkflowKind.QUERY):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(blocked())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with controller.workflow(WorkflowKind.QUERY):
            pass

    asyncio.run(run())


def test_cancellation_keeps_permits_while_a_provider_thread_drains() -> None:
    async def run() -> None:
        controller = WorkflowController(
            ExecutionLimits(model_in_flight_limit=1, mcp_in_flight_limit=1)
        )
        provider_started = threading.Event()
        release_provider = threading.Event()

        def blocking_provider() -> None:
            provider_started.set()
            release_provider.wait()

        async def invoke() -> None:
            async with controller.workflow(WorkflowKind.QUERY):
                await run_blocking_provider_call(blocking_provider)

        task = asyncio.create_task(invoke())
        while not provider_started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(WorkflowOverloaded):
            await controller.admit(WorkflowKind.QUERY)

        drain = asyncio.create_task(controller.drain_provider_calls())
        await asyncio.sleep(0)
        assert not drain.done()

        release_provider.set()
        await drain
        while True:
            try:
                lease = await controller.admit(WorkflowKind.QUERY)
            except WorkflowOverloaded:
                await asyncio.sleep(0)
            else:
                async with lease:
                    pass
                break

    asyncio.run(run())


def test_execution_limits_reject_non_finite_deadlines() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        ExecutionLimits(deadline_seconds=float("inf"))


def test_safe_workflow_telemetry_has_no_request_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        controller = WorkflowController(ExecutionLimits())
        async with controller.workflow(WorkflowKind.BACKTEST):
            pass

    with caplog.at_level(logging.INFO, logger="sherlock.execution"):
        asyncio.run(run())

    assert "kind=backtest outcome=ok elapsed_ms=" in caplog.text


def test_exceptional_workflow_has_a_failed_telemetry_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        controller = WorkflowController(ExecutionLimits())
        with pytest.raises(RuntimeError, match="dependency failed"):
            async with controller.workflow(WorkflowKind.BACKTEST):
                raise RuntimeError("dependency failed")

    with caplog.at_level(logging.INFO, logger="sherlock.execution"):
        asyncio.run(run())

    assert "kind=backtest outcome=failed elapsed_ms=" in caplog.text


def test_query_timeout_has_a_safe_gateway_timeout_contract() -> None:
    class SlowService:
        async def query(self, question: str) -> dict[str, object]:
            await asyncio.sleep(0.02)
            return {}

    async def run() -> None:
        controller = WorkflowController(ExecutionLimits(deadline_seconds=0.001))
        with pytest.raises(HTTPException) as error:
            await query(
                QueryRequest(question="Count transactions"),
                cast(Text2SQLService, SlowService()),
                controller,
            )
        assert error.value.status_code == 504
        assert error.value.detail == (
            "Sherlock could not complete the request in time. Please try again."
        )

    asyncio.run(run())


def test_open_sse_stream_reports_a_safe_deadline_error() -> None:
    async def events():
        await asyncio.sleep(0.02)
        yield "text_delta", ChatTextDelta(delta="not delivered", segment="content")

    async def run() -> list[str]:
        controller = WorkflowController(ExecutionLimits(deadline_seconds=0.001))
        lease = await controller.admit(WorkflowKind.CHAT)
        return [event async for event in _traced_chat_event_stream(events(), 0, lease)]

    emitted = asyncio.run(run())

    assert len(emitted) == 1
    assert "event: error" in emitted[0]
    assert "could not complete the request in time" in emitted[0]


def test_sse_deadline_does_not_depend_on_response_writes() -> None:
    async def events():
        yield "text_delta", ChatTextDelta(delta="first", segment="content")
        await asyncio.sleep(0.02)
        yield "text_delta", ChatTextDelta(delta="not delivered", segment="content")

    async def run() -> list[str]:
        controller = WorkflowController(ExecutionLimits(deadline_seconds=0.001))
        lease = await controller.admit(WorkflowKind.CHAT)
        stream = _traced_chat_event_stream(events(), 0, lease)
        first = await anext(stream)
        await asyncio.sleep(0.02)  # Simulate an ASGI response write under backpressure.
        remaining = [event async for event in stream]
        return [first, *remaining]

    emitted = asyncio.run(run())

    assert "first" in emitted[0]
    assert "event: error" in emitted[1]


def test_handled_sse_error_has_a_failed_telemetry_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def events():
        raise ChatAgentError("provider unavailable")
        yield  # pragma: no cover - makes this an async generator

    async def run() -> list[str]:
        controller = WorkflowController(ExecutionLimits())
        lease = await controller.admit(WorkflowKind.CHAT)
        return [event async for event in _traced_chat_event_stream(events(), 0, lease)]

    with caplog.at_level(logging.INFO, logger="sherlock.execution"):
        emitted = asyncio.run(run())

    assert "event: error" in emitted[0]
    assert "kind=chat outcome=failed elapsed_ms=" in caplog.text


def test_sse_buffer_bounds_events_and_bytes() -> None:
    buffer = StreamBuffer()
    for _ in range(MAX_STREAM_BUFFERED_EVENTS):
        buffer.put("event")

    with pytest.raises(StreamBufferFull):
        buffer.put("one event too many")

    bytes_buffer = StreamBuffer()
    with pytest.raises(StreamBufferFull):
        bytes_buffer.put("x" * (MAX_STREAM_BUFFERED_BYTES + 1))
