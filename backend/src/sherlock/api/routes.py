"""FastAPI routes kept separate from Text2SQL business logic."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sherlock.api.schemas import (
    BacktestResponse,
    QueryRequest,
    QueryResponse,
    RuleGenerateRequest,
    RuleGenerateResponse,
    RuleRefineRequest,
    RuleRefineResponse,
    RuleRequest,
)
from sherlock.services.backtest import (
    BacktestError,
    BacktestService,
    InvalidBacktestRule,
)
from sherlock.services.rule_generation import (
    InvalidCurrentRule,
    RuleGenerationError,
    RuleGenerationService,
)
from sherlock.services.text2sql import Text2SQLError, Text2SQLService

router = APIRouter(prefix="/v1")


def get_text2sql_service(request: Request) -> Text2SQLService:
    """Return the application-scoped Text2SQL service."""

    return request.app.state.text2sql_service


Text2SQLDependency = Annotated[Text2SQLService, Depends(get_text2sql_service)]


def get_rule_generation_service(request: Request) -> RuleGenerationService:
    """Return the application-scoped candidate-rule service."""

    return request.app.state.rule_generation_service


RuleGenerationDependency = Annotated[
    RuleGenerationService, Depends(get_rule_generation_service)
]


def get_backtest_service(request: Request) -> BacktestService:
    return request.app.state.backtest_service


BacktestDependency = Annotated[BacktestService, Depends(get_backtest_service)]


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    service: Text2SQLDependency,
) -> QueryResponse:
    """Translate a natural-language question to SQL and execute it."""

    try:
        result = await service.query(request.question)
    except Text2SQLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return QueryResponse.model_validate(result)


@router.post("/rules/generate", response_model=RuleGenerateResponse)
async def generate_rule(
    request: RuleGenerateRequest,
    service: RuleGenerationDependency,
) -> RuleGenerateResponse:
    """Generate and deterministically validate a candidate fraud rule."""

    try:
        result = await service.generate(request.instruction)
    except (RuleGenerationError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return RuleGenerateResponse.model_validate(result)


@router.post("/rules/refine", response_model=RuleRefineResponse)
async def refine_rule(
    request: RuleRefineRequest,
    service: RuleGenerationDependency,
) -> RuleRefineResponse:
    """Apply a contextual modification to an explicit candidate rule."""

    try:
        result = await service.refine(request.rule, request.instruction)
    except InvalidCurrentRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.validation.as_dict(),
        ) from exc
    except RuleGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return RuleRefineResponse.model_validate(result)


@router.post("/rules/backtest", response_model=BacktestResponse)
async def backtest_rule(
    request: RuleRequest,
    service: BacktestDependency,
) -> BacktestResponse:
    """Replay a candidate rule against historical transactions."""

    try:
        result = await service.backtest(request.rule)
    except InvalidBacktestRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.validation.as_dict(),
        ) from exc
    except BacktestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return BacktestResponse.model_validate(result)
