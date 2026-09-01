"""Bounded, safe admission control for expensive backend workflows."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType

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
        if self.deadline_seconds <= 0:
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

    async def __aenter__(self) -> None:
        self._deadline = asyncio.timeout(self._controller.limits.deadline_seconds)
        await self._deadline.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            assert self._deadline is not None
            await self._deadline.__aexit__(exc_type, exc, traceback)
        except TimeoutError as timeout:
            self._controller._record(self._kind, "deadline_exceeded", self._started)
            raise WorkflowDeadlineExceeded from timeout
        else:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "ok"
            self._controller._record(self._kind, outcome, self._started)
        finally:
            for permit in reversed(self._permits):
                permit.release()


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
