from __future__ import annotations

import os
from typing import Any

import pytest

from sherlock.config import Settings
from sherlock.telemetry import configure_tracing


def test_configure_tracing_passes_phoenix_bearer_header_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bearer tokens must not pass through OTLP's URL-encoded env parser."""

    captured: dict[str, Any] = {}

    class FakeExporter:
        def __init__(self, **kwargs: Any) -> None:
            captured["exporter"] = kwargs

    class FakeProcessor:
        def __init__(self, exporter: FakeExporter) -> None:
            captured["processor_exporter"] = exporter

    class FakeProvider:
        def __init__(self, **kwargs: Any) -> None:
            captured["provider"] = kwargs

        def add_span_processor(self, processor: FakeProcessor) -> None:
            captured["processor"] = processor

    monkeypatch.setattr(
        "sherlock.telemetry._phoenix_configuration",
        lambda _settings: ("https://app.phoenix.arize.com/v1/traces", "test-key"),
    )
    monkeypatch.setattr("sherlock.telemetry.OTLPSpanExporter", FakeExporter)
    monkeypatch.setattr("sherlock.telemetry.BatchSpanProcessor", FakeProcessor)
    monkeypatch.setattr("sherlock.telemetry.TracerProvider", FakeProvider)
    monkeypatch.setattr("sherlock.telemetry.trace.set_tracer_provider", lambda _: None)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)

    configure_tracing(Settings())

    assert captured["exporter"] == {
        "endpoint": "https://app.phoenix.arize.com/v1/traces",
        "headers": {"authorization": "Bearer test-key"},
    }
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in os.environ
