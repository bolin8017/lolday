"""A separate FastAPI app instance that hosts ONLY /api/v1/internal/*.

Bound to container port 8001 by the entrypoint. NetworkPolicy gates :8001
to lolday-jobs (callbacks) only; Cloudflared tunnel maps :8000 only.
"""

from fastapi import FastAPI

from app.routers import internal

internal_app = FastAPI(title="Lolday Internal", docs_url=None, redoc_url=None)
internal_app.include_router(
    internal.router,
    prefix="/api/v1/internal",
    tags=["internal"],
)


@internal_app.get("/livez", include_in_schema=False)
async def livez():
    return {"status": "ok"}
