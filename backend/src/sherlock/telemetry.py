"""Safe, fail-open OpenTelemetry configuration for backend request traces."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, StatusCode

from sherlock.config import Settings

logger = logging.getLogger(__name__)
TRACER_NAME = "sherlock.backend"
CHAT_TURN_SPAN = "sherlock.chat.turn"

_OTEL_DEFAULTS = {
    "OTEL_SERVICE_NAME": "sherlock-backend",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_BSP_SCHEDULE_DELAY": "5000",
    "OTEL_BSP_EXPORT_TIMEOUT": "10000",
    "OTEL_BSP_MAX_QUEUE_SIZE": "2048",
    "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "512",
    "OTEL_EXPORTER_OTLP_TIMEOUT": "10000",
    "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "10000",
    "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
    "OTEL_TRACES_SAMPLER_ARG": "1.0",
    # Strands honours this setting by redacting prompt, model, and tool content.
    "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_unredacted_attributes=",
}


@dataclass
class SpanOutcome:
    """A safe failure signal for a handled error that must not be re-raised."""

    error_type: str | None = None

    def fail(self, exception: BaseException) -> None:
        self.error_type = type(exception).__name__


def _phoenix_configuration(settings: Settings) -> tuple[str, str] | None:
    """Read Phoenix collector credentials from the runtime Secret Manager secret."""

    if not settings.phoenix_secret_id:
        return None
    try:
        value = boto3.client(
            "secretsmanager",
            config=Config(
                connect_timeout=1,
                read_timeout=2,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        ).get_secret_value(SecretId=settings.phoenix_secret_id)["SecretString"]
    except (BotoCoreError, ClientError, KeyError, TypeError):
        logger.warning("Phoenix tracing is disabled: runtime secret is unavailable")
        return None
    try:
        import json

        secret = json.loads(value)
        endpoint = secret["PHOENIX_ENDPOINT"].strip()
        api_key = secret["PHOENIX_API_KEY"].strip()
    except (AttributeError, KeyError, TypeError, ValueError):
        logger.warning("Phoenix tracing is disabled: runtime secret is invalid")
        return None
    if (
        not endpoint.startswith(("https://", "http://"))
        or not endpoint.endswith("/v1/traces")
        or not api_key
    ):
        logger.warning("Phoenix tracing is disabled: runtime secret is incomplete")
        return None
    return endpoint, api_key


def configure_tracing(settings: Settings) -> None:
    """Install the shared provider before Strands agents are constructed.

    Exporter setup deliberately fails open: a collector outage or malformed secret
    leaves in-process tracing harmlessly disabled and cannot prevent API startup.
    """

    for name, value in _OTEL_DEFAULTS.items():
        os.environ.setdefault(name, value)
    # Never permit an inherited opt-in to override the content-redaction policy.
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_unredacted_attributes="

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "sherlock-backend",
                "service.version": "0.1.0",
            }
        )
    )
    trace.set_tracer_provider(provider)
    configuration = _phoenix_configuration(settings)
    if configuration is None:
        return
    endpoint, api_key = configuration
    try:
        # Use the standard OTLP environment contract so the exporter, batch
        # processor, and deployment configuration are all inspectable without
        # coupling Sherlock to Phoenix's client library. Do not log either value.
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = endpoint
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Bearer {api_key}"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    except Exception:  # noqa: BLE001 - defensive vendor boundary
        logger.warning("Phoenix tracing is disabled: exporter setup failed")


def shutdown_tracing() -> None:
    """Flush bounded batches on shutdown without affecting shutdown semantics."""

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return
    try:
        provider.shutdown()
    except Exception:  # noqa: BLE001 - defensive vendor boundary
        logger.warning("Phoenix tracing shutdown failed")


@asynccontextmanager
async def span(
    name: str,
    *,
    attributes: Mapping[str, str | int | bool] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    outcome: SpanOutcome | None = None,
) -> AsyncIterator[None]:
    """Create a safe nested span and record only the exception class on failure."""

    tracer = trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span(
        name,
        kind=kind,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        if attributes:
            current.set_attributes(attributes)
        try:
            yield
        except BaseException as exc:
            current.set_status(StatusCode.ERROR, type(exc).__name__)
            current.set_attribute("error.type", type(exc).__name__)
            raise
        else:
            if outcome is not None and outcome.error_type is not None:
                current.set_status(StatusCode.ERROR, outcome.error_type)
                current.set_attribute("error.type", outcome.error_type)
            else:
                current.set_status(StatusCode.OK)
