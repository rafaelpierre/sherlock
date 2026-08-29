"""FastAPI routes kept separate from Text2SQL business logic."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sherlock.api.schemas import QueryRequest, QueryResponse
from sherlock.services.text2sql import Text2SQLError, Text2SQLService

router = APIRouter(prefix="/v1")


def get_text2sql_service(request: Request) -> Text2SQLService:
    """Return the application-scoped Text2SQL service."""

    return request.app.state.text2sql_service


Text2SQLDependency = Annotated[Text2SQLService, Depends(get_text2sql_service)]


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
