"""MLflow model-registry stage sync.

The MLflow REST API allows external clients (e.g. an ML-Ops engineer using
the MLflow UI) to transition model versions between stages
(None → Staging → Production → Archived). :func:`sync_model_versions`
runs every ~60s from :func:`reconciler_loop` and reflects those
transitions back into the lolday DB so the UI shows current state.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.mlflow_client import MlflowClient


async def sync_model_versions(session: AsyncSession) -> None:
    """Pull latest stages from MLflow; reflect transitions initiated outside lolday."""
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    from app.models import ModelVersion
    from app.models.model_registry import ModelVersionStage

    all_local = (await session.execute(select(ModelVersion))).scalars().all()
    if not all_local:
        return

    remote = await client.search_model_versions()
    by_key = {(m["name"], int(m["version"])): m for m in remote}

    for mv in all_local:
        rem = by_key.get((mv.mlflow_name, mv.mlflow_version))  # type: ignore[attr-defined]  # mlflow_name moved to RegisteredModel.mlflow_name (property); rewrite in T15
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
