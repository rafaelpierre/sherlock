"""Bounded, safe admission control for expensive backend workflows."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowKind(StrEnum):
    """Resource classes used by production HTTP workflows."""

    QUERY = "query"
    RULE_GENERATION = "rule_generation"
    BACKTEST = "backtest"
    COMPARISON = "comparison"
    CHAT = "chat"


_MODEL_WORKFLOWS = {
    WorkflowKind.QUERY,
    WorkflowKind.RULE_GENERATION,
    WorkflowKind.CHAT,
}


class WorkflowOverloaded(Exception):
    """The worker has no spare capacity for this expensive workflow."""


class WorkflowDeadlineExceeded(Exception):
    """The workflow did not finish within its configured end-to-end budget."""


@dataclass(frozen=True)
class ExecutionLimits:
    """Validated per-worker execution limits, in seconds and work items."""

    deadline_seconds: float = 240.0
    model_in_flight_limit: int = 8
    mcp_in_flight_limit: int = 16

    def __post_init__(self) -> None:
        if not math.isfinite(self.deadline_seconds) or self.deadline_seconds <= 0:
            raise ValueError("workflow deadline must be greater than zero")
        if self.model_in_flight_limit < 1:
            raise ValueError("model in-flight limit must be at least 1")
        if self.mcp_in_flight_limit < 1:
            raise ValueError("MCP in-flight limit must be at least 1")


class WorkflowLease(AbstractAsyncContextManager[None]):
    """An admitted workflow, including its deadline and permit cleanup."""

    def __init__(
        self,
        controller: WorkflowController,
        kind: WorkflowKind,
        permits: tuple[asyncio.Semaphore, ...],
    ) -> None:
        self._controller = controller
        self._kind = kind
        self._permits = permits
        self._started = time.monotonic()
        self._deadline: asyncio.Timeout | None = None
        self._context_token: Token[WorkflowLease | None] | None = None
        self._blocking_provider_tasks: set[asyncio.Task[Any]] = set()
        self._released = False

    async def __aenter__(self) -> None:
        self._context_token = _active_workflow_lease.set(self)
        self._deadline = asyncio.timeout(self._controller.limits.deadline_seconds)
        await self._deadline.__aenter__()

    def track_blocking_provider_task(self, task: asyncio.Task[Any]) -> None:
        """Keep admission capacity while an uncancellable provider thread drains."""

        self._blocking_provider_tasks.add(task)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        outcome = "ok"
        try:
            assert self._deadline is not None
            await self._deadline.__aexit__(exc_type, exc, traceback)
        except TimeoutError as timeout:
            outcome = "deadline_exceeded"
            raise WorkflowDeadlineExceeded from timeout
        else:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "ok"
        finally:
            if self._context_token is not None:
                _active_workflow_lease.reset(self._context_token)
            pending = tuple(
                task for task in self._blocking_provider_tasks if not task.done()
            )
            if pending:
                asyncio.create_task(
                    self._release_after_provider_drain(pending, outcome)
                )
            else:
                self._release(outcome)

    async def _release_after_provider_drain(
        self, tasks: tuple[asyncio.Task[Any], ...], outcome: str
    ) -> None:
        await asyncio.gather(*tasks, return_exceptions=True)
        self._release(outcome)

    def _release(self, outcome: str) -> None:
        if self._released:
            return
        self._released = True
        self._controller._record(self._kind, outcome, self._started)
        for permit in reversed(self._permits):
            permit.release()


_active_workflow_lease: ContextVar[WorkflowLease | None] = ContextVar(
    "active_workflow_lease", default=None
)


async def run_blocking_provider_call[T](function: Callable[..., T], *args: Any) -> T:
    """Run blocking provider work without losing its admission reservation.

    Cancelling this coroutine leaves the thread running, so its current workflow
    lease retains model/MCP capacity until that thread has actually completed.
    """

    task = asyncio.create_task(asyncio.to_thread(function, *args))
    if lease := _active_workflow_lease.get():
        lease.track_blocking_provider_task(task)
    return await asyncio.shield(task)


class WorkflowController:
    """Per-worker, non-queuing admission for model and MCP-backed work.

    Each workflow reserves MCP capacity. Workflows that can invoke a model also
    reserve model capacity. This deliberately rejects excess work immediately:
    a worker never accumulates an unbounded local queue while a provider is slow.
    """

    def __init__(self, limits: ExecutionLimits) -> None:
        self.limits = limits
        self._model = asyncio.Semaphore(limits.model_in_flight_limit)
        self._mcp = asyncio.Semaphore(limits.mcp_in_flight_limit)

    async def admit(self, kind: WorkflowKind) -> WorkflowLease:
        permits = (self._mcp,)
        if kind in _MODEL_WORKFLOWS:
            permits = (self._model, self._mcp)
        acquired: list[asyncio.Semaphore] = []
        for permit in permits:
            if permit.locked():
                for held in reversed(acquired):
                    held.release()
                self._record(kind, "overloaded", time.monotonic())
                raise WorkflowOverloaded
            await permit.acquire()
            acquired.append(permit)
        return WorkflowLease(self, kind, tuple(acquired))

    @asynccontextmanager
    async def workflow(self, kind: WorkflowKind) -> AsyncIterator[None]:
        lease = await self.admit(kind)
        async with lease:
            yield

    def _record(self, kind: WorkflowKind, outcome: str, started: float) -> None:
        # Do not add request content, SQL, rows, tool arguments, or provider data.
        logger.info(
            "workflow_finished kind=%s outcome=%s elapsed_ms=%d",
            kind,
            outcome,
            round((time.monotonic() - started) * 1000),
        )
