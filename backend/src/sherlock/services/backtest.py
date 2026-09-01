"""Deterministic historical replay for validated candidate fraud rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx

from sherlock.config import Settings
from sherlock.services.rule_validation import (
    MCPSchemaProvider,
    RuleSchemaError,
    RuleValidationResult,
    RuleValidationService,
    RuleValidator,
)
from sherlock.services.text2sql import (
    MCPQueryExecutor,
    QueryExecutionError,
    QueryExecutor,
)


class BacktestError(RuntimeError):
    """Historical replay could not be completed."""


class InvalidBacktestRule(BacktestError):
    """The supplied candidate rule failed deterministic validation."""

    def __init__(self, validation: RuleValidationResult) -> None:
        super().__init__("Candidate rule is invalid.")
        self.validation = validation


@dataclass(frozen=True)
class BacktestMetrics:
    population: int
    labelled_population: int
    fraud_total: int
    transactions_flagged: int
    unlabelled_flagged: int
    fraud_caught: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float | None
    recall: float | None
    false_positive_rate: float | None
    fraud_value_total_usd: float
    fraud_value_captured_usd: float
    fraud_value_recall: float | None
    alerts_per_day: float | None


@dataclass(frozen=True)
class BacktestResult:
    rule: str
    metrics: BacktestMetrics


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


class BacktestService:
    """Validate and replay a rule through the read-only MCP boundary."""

    def __init__(
        self,
        validator: RuleValidator,
        executor: QueryExecutor,
        *,
        start_callback: Any = None,
        close_callback: Any = None,
    ) -> None:
        self._validator = validator
        self._executor = executor
        self._start_callback = start_callback
        self._close_callback = close_callback
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        if self._start_callback is not None:
            await self._start_callback()
        self._started = True

    async def backtest(self, rule: str) -> dict[str, Any]:
        try:
            await self.start()
        except* (httpx.HTTPError, OSError) as exc:
            raise BacktestError("The MCP service is unavailable.") from exc
        try:
            validation = await self._validator.validate(rule)
        except (QueryExecutionError, RuleSchemaError) as exc:
            raise BacktestError(str(exc)) from exc
        if not validation.valid or validation.rule is None:
            raise InvalidBacktestRule(validation)

        normalized_rule = validation.rule
        try:
            execution = await self._executor.execute(self._query(normalized_rule))
        except QueryExecutionError as exc:
            raise BacktestError(str(exc)) from exc
        if execution.error is not None:
            message = execution.error.get(
                "message", "Historical replay could not be executed."
            )
            raise BacktestError(str(message))
        if execution.data is None or len(execution.data.rows) != 1:
            raise BacktestError("Historical replay returned invalid aggregate data.")

        values = dict(zip(execution.data.columns, execution.data.rows[0], strict=True))
        try:
            population = int(values["population"])
            labelled_population = int(values["labelled_population"])
            fraud_total = int(values["fraud_total"])
            transactions_flagged = int(values["transactions_flagged"])
            unlabelled_flagged = int(values["unlabelled_flagged"])
            fraud_caught = int(values["fraud_caught"])
            false_positives = int(values["false_positives"])
            false_negatives = int(values["false_negatives"])
            true_negatives = int(values["true_negatives"])
            fraud_value_total = float(values["fraud_value_total_usd"])
            fraud_value_captured = float(values["fraud_value_captured_usd"])
            observed_days = int(values["observed_days"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BacktestError(
                "Historical replay returned invalid aggregate data."
            ) from exc

        metrics = BacktestMetrics(
            population=population,
            labelled_population=labelled_population,
            fraud_total=fraud_total,
            transactions_flagged=transactions_flagged,
            unlabelled_flagged=unlabelled_flagged,
            fraud_caught=fraud_caught,
            false_positives=false_positives,
            false_negatives=false_negatives,
            true_negatives=true_negatives,
            precision=_ratio(fraud_caught, fraud_caught + false_positives),
            recall=_ratio(fraud_caught, fraud_caught + false_negatives),
            false_positive_rate=_ratio(
                false_positives, false_positives + true_negatives
            ),
            fraud_value_total_usd=fraud_value_total,
            fraud_value_captured_usd=fraud_value_captured,
            fraud_value_recall=_ratio(fraud_value_captured, fraud_value_total),
            alerts_per_day=_ratio(transactions_flagged, observed_days),
        )
        return asdict(BacktestResult(normalized_rule, metrics))

    @staticmethod
    def _query(rule: str) -> str:
        return f"""
            WITH evaluated AS (
                SELECT
                    is_fraud,
                    amount_usd,
                    transaction_date,
                    CASE WHEN ({rule}) THEN 1 ELSE 0 END AS flagged
                FROM fraud_transactions
            )
            SELECT
                COUNT(*) AS population,
                COALESCE(SUM(is_fraud IS NOT NULL), 0) AS labelled_population,
                COALESCE(SUM(is_fraud = 1), 0) AS fraud_total,
                COALESCE(SUM(flagged), 0) AS transactions_flagged,
                COALESCE(SUM(flagged = 1 AND is_fraud IS NULL), 0)
                    AS unlabelled_flagged,
                COALESCE(SUM(flagged = 1 AND is_fraud = 1), 0) AS fraud_caught,
                COALESCE(SUM(flagged = 1 AND is_fraud = 0), 0) AS false_positives,
                COALESCE(SUM(flagged = 0 AND is_fraud = 1), 0) AS false_negatives,
                COALESCE(SUM(flagged = 0 AND is_fraud = 0), 0) AS true_negatives,
                COALESCE(SUM(CASE WHEN is_fraud = 1 THEN amount_usd ELSE 0 END), 0)
                    AS fraud_value_total_usd,
                COALESCE(SUM(CASE WHEN flagged = 1 AND is_fraud = 1
                    THEN amount_usd ELSE 0 END), 0) AS fraud_value_captured_usd,
                COUNT(DISTINCT transaction_date) AS observed_days
            FROM evaluated
        """.strip()

    def close(self) -> None:
        if self._close_callback is not None:
            self._close_callback()


def create_backtest_service(settings: Settings) -> BacktestService:
    """Build the production backtest service and its MCP resource."""

    client = settings.mcp_client(allowed_tools=("get_schema", "run_query"))
    owner = object()
    client.add_consumer(owner)
    executor = MCPQueryExecutor(client)
    validator = RuleValidationService(MCPSchemaProvider(client), executor)

    def close_client() -> None:
        client.remove_consumer(owner)

    return BacktestService(
        validator,
        executor,
        start_callback=client.load_tools,
        close_callback=close_client,
    )
