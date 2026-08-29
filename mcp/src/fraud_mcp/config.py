"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    """Validated server settings."""

    database_path: Path = PROJECT_ROOT / "db" / "data" / "data.db"
    max_query_rows: int = Field(default=100, ge=1)
    hard_max_query_rows: int = Field(default=1000, ge=1)
    query_timeout_seconds: float = Field(default=10.0, gt=0)
    log_level: str = "INFO"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8000, ge=1, le=65535)
    mcp_path: str = "/mcp"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"unsupported log level: {value}")
        return level

    @field_validator("mcp_path")
    @classmethod
    def normalize_mcp_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("MCP_PATH must not be empty")
        return "/" + value.strip("/")

    @model_validator(mode="after")
    def validate_limits(self) -> Settings:
        if self.max_query_rows > self.hard_max_query_rows:
            raise ValueError("MAX_QUERY_ROWS cannot exceed HARD_MAX_QUERY_ROWS")
        return self

    @classmethod
    def from_environment(cls) -> Settings:
        names = {
            "database_path": "DATABASE_PATH",
            "max_query_rows": "MAX_QUERY_ROWS",
            "hard_max_query_rows": "HARD_MAX_QUERY_ROWS",
            "query_timeout_seconds": "QUERY_TIMEOUT_SECONDS",
            "log_level": "LOG_LEVEL",
            "mcp_host": "MCP_HOST",
            "mcp_port": "MCP_PORT",
            "mcp_path": "MCP_PATH",
        }
        values = {
            field: os.environ[environment]
            for field, environment in names.items()
            if environment in os.environ
        }
        return cls.model_validate(values)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_environment()
