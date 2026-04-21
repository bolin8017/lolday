"""Typed error payloads for HTTPException.detail.

FastAPI treats `HTTPException.detail` as an opaque JSON value, so raw
dict literals are common — but they drift silently. These Pydantic
models make the shape an import-time contract and let the generated
OpenAPI schema (and the frontend type generator) see the field set.

Usage:

    from app.schemas.errors import ConcurrencyLimitDetail
    raise HTTPException(
        status_code=429,
        detail=ConcurrencyLimitDetail(limit=N, in_flight=M).model_dump(),
    )
"""

from typing import Literal

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Base for all typed HTTPException.detail payloads."""

    code: str
    message: str


class ConcurrencyLimitDetail(ErrorDetail):
    code: Literal["concurrency_limit"] = "concurrency_limit"
    message: str = "too many in-flight builds"
    limit: int
    in_flight: int
