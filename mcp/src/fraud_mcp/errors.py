"""Domain errors returned by MCP tools."""

from __future__ import annotations

from enum import StrEnum


class ErrorType(StrEnum):
    INVALID_SQL = "INVALID_SQL"
    UNSAFE_SQL = "UNSAFE_SQL"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    UNKNOWN_RELATION = "UNKNOWN_RELATION"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_EXECUTION_ERROR = "QUERY_EXECUTION_ERROR"
    INVALID_TOOL_ARGUMENT = "INVALID_TOOL_ARGUMENT"


class AnalyticsError(Exception):
    """An expected failure safe to present to an MCP caller."""

    def __init__(
        self,
        error_type: ErrorType,
        message: str,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.suggestion = suggestion

    def as_dict(self) -> dict[str, object]:
        detail: dict[str, object] = {
            "type": self.error_type.value,
            "message": self.message,
        }
        if self.suggestion:
            detail["suggestion"] = self.suggestion
        return {"error": detail}
