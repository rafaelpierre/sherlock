"""FastAPI application declaration and runtime entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from sherlock.api.auth import CognitoTokenVerifier
from sherlock.api.routes import router
from sherlock.chat import ChatAgentFactory
from sherlock.config import Settings
from sherlock.execution import ExecutionLimits, WorkflowController
from sherlock.services.backtest import create_backtest_service
from sherlock.services.rule_comparison import RuleComparisonService
from sherlock.services.rule_generation import create_rule_generation_service
from sherlock.services.text2sql import create_text2sql_service
from sherlock.telemetry import configure_tracing, shutdown_tracing


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Create application-scoped resources and close them on shutdown."""

    settings = Settings.from_environment()
    application.state.auth_verifier = (
        CognitoTokenVerifier(settings.cognito_issuer, settings.cognito_client_id)
        if settings.auth_required
        and settings.cognito_issuer
        and settings.cognito_client_id
        else None
    )
    configure_tracing(settings)
    text2sql_service = create_text2sql_service(settings)
    rule_generation_service = create_rule_generation_service(settings)
    backtest_service = create_backtest_service(settings)
    rule_comparison_service = RuleComparisonService(backtest_service)
    chat_agent_factory = ChatAgentFactory(
        text2sql_service,
        rule_generation_service,
        backtest_service,
        rule_comparison_service,
    )
    application.state.workflow_controller = WorkflowController(
        ExecutionLimits(
            deadline_seconds=settings.workflow_deadline_seconds,
            model_in_flight_limit=settings.model_in_flight_limit,
            mcp_in_flight_limit=settings.mcp_in_flight_limit,
        )
    )
    application.state.text2sql_service = text2sql_service
    application.state.rule_generation_service = rule_generation_service
    application.state.backtest_service = backtest_service
    application.state.rule_comparison_service = rule_comparison_service
    application.state.chat_agent_factory = chat_agent_factory
    try:
        yield
    finally:
        await application.state.workflow_controller.drain_provider_calls()
        backtest_service.close()
        rule_generation_service.close()
        text2sql_service.close()
        shutdown_tracing()


def create_app() -> FastAPI:
    """Create the Sherlock HTTP application."""

    application = FastAPI(
        title="Sherlock Fraud Analytics API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router)

    @application.middleware("http")
    async def require_cognito_access_token(request: Request, call_next):
        """Protect product endpoints before request parsing or workflow creation."""

        if request.url.path.startswith("/v1/") and request.url.path != "/v1/health":
            verifier: CognitoTokenVerifier | None = getattr(
                request.app.state, "auth_verifier", None
            )
            if verifier is not None:
                try:
                    request.state.auth_claims = await verifier.verify_authorization(
                        request.headers.get("authorization")
                    )
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers,
                    )
        return await call_next(request)

    return application


app = create_app()


def run() -> None:
    """Run the API with development-friendly defaults."""

    uvicorn.run("sherlock.api.app:app", host="0.0.0.0", port=8080)
