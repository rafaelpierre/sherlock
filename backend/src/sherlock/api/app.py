"""FastAPI application declaration and runtime entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from sherlock.api.routes import router
from sherlock.config import Settings
from sherlock.services.text2sql import create_text2sql_service


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Create application-scoped resources and close them on shutdown."""

    service = create_text2sql_service(Settings.from_environment())
    application.state.text2sql_service = service
    try:
        yield
    finally:
        service.close()


def create_app() -> FastAPI:
    """Create the Sherlock HTTP application."""

    application = FastAPI(
        title="Sherlock Text2SQL API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()


def run() -> None:
    """Run the API with development-friendly defaults."""

    uvicorn.run("sherlock.api.app:app", host="0.0.0.0", port=8080)
