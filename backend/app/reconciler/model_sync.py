"""MLflow model-registry stage sync.

The MLflow REST API allows external clients (e.g. an ML-Ops engineer using
the MLflow UI) to transition model versions between stages
(None → Staging → Production → Archived). :func:`sync_model_versions`
runs every ~60s from :func:`reconciler_loop` and reflects those
transitions back into the lolday DB so the UI shows current state.
"""

from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings
from app.services.mlflow_client import MlflowClient


async def sync_model_versions(
    session: AsyncSession, mlflow: MlflowClient | None = None
) -> None:
    """Pull latest stages from MLflow; reflect transitions initiated outside lolday."""
    # In production ``mlflow`` is always the lifespan-owned client. The ``None``
    # fallback is for backward-compat test call sites without the mlflow arg.
    client = (
        mlflow
        if mlflow is not None
        else MlflowClient(
            settings.MLFLOW_TRACKING_URI,
            http_client=httpx.AsyncClient(timeout=httpx.Timeout(10.0)),
        )
    )
    from app.models import ModelVersion
    from app.models.model_registry import ModelVersionStage, RegisteredModel

    # Eagerly load registered_model → owner and registered_model → detector so
    # that rm.mlflow_name (a derived property: f"{owner.handle}/{detector.name}")
    # is fully resolved without triggering async lazy-loads in the loop below.
    all_local = (
        (
            await session.execute(
                select(ModelVersion).options(
                    joinedload(ModelVersion.registered_model).options(
                        joinedload(RegisteredModel.owner),
                        joinedload(RegisteredModel.detector),
                    )
                )
            )
        )
        .scalars()
        .all()
    )

    if not all_local:
        return

    remote = await client.search_model_versions()
    by_key = {(m["name"], int(m["version"])): m for m in remote}

    for mv in all_local:
        rm: RegisteredModel = mv.registered_model
        mlflow_name = f"{rm.owner.handle}/{rm.detector.name}"
        rem = by_key.get((mlflow_name, mv.mlflow_version))
        if rem is None:
            continue
        remote_stage = rem.get("current_stage", "None")
        try:
            stage_enum = ModelVersionStage(remote_stage)
        except ValueError:
            continue
        if stage_enum != mv.current_stage:
            mv.current_stage = stage_enum
            mv.last_transitioned_at = datetime.now(UTC)
    await session.commit()
