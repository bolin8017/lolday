from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


class MlflowError(Exception):
    pass


class MlflowClient:
    """Async thin REST wrapper for MLflow Tracking + Model Registry.

    We don't import mlflow-skinny's own client because it's sync; we reuse httpx
    for backend-wide async consistency. Endpoints per MLflow 2.20 REST API.

    Construction: always requires a caller-owned ``httpx.AsyncClient``; its
    lifetime is managed by the caller (FastAPI lifespan via ``app.state.http``
    in production, or a test-local client in unit tests).

    Preferred usage: ``MlflowClient.from_settings(settings, http_client)`` in the
    FastAPI lifespan, then inject via ``Depends(get_mlflow)`` in handlers and
    as a function argument in background tasks.
    """

    def __init__(
        self,
        tracking_uri: str,
        timeout: float = 10.0,
        retries: int = 3,
        *,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._base = tracking_uri.rstrip("/")
        self._timeout = httpx.Timeout(timeout)
        self._retries = retries
        self._http: httpx.AsyncClient = http_client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> MlflowClient:
        """Construct an MlflowClient from app settings + a caller-owned AsyncClient.

        Intended for use in ``app/main.py`` lifespan:

            app.state.mlflow = MlflowClient.from_settings(settings, http)

        The ``http_client`` lifetime is managed by the caller (typically the
        FastAPI lifespan context).
        """
        return cls(
            settings.MLFLOW_TRACKING_URI,
            timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS,
            retries=3,
            http_client=http_client,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}/api/2.0/mlflow{path}"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = await self._http.request(method, url, json=json, params=params)
                if resp.status_code >= 400:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = {"error_code": "UNKNOWN", "message": resp.text}
                    return self._handle_error(resp.status_code, body)
                return resp.json() if resp.content else {}
            except httpx.HTTPError as e:
                last_exc = e
                await asyncio.sleep(0.2 * (attempt + 1))
        raise MlflowError(f"network error after {self._retries} retries: {last_exc!r}")

    def _handle_error(self, status: int, body: dict) -> dict:
        code = body.get("error_code", "UNKNOWN")
        msg = body.get("message", "")
        e = MlflowError(f"{code}: {msg}")
        e.code = code  # type: ignore[attr-defined]  # dynamic attribute for error context
        e.http_status = status  # type: ignore[attr-defined]  # dynamic attribute for error context
        raise e

    # experiments
    async def create_experiment(
        self, name: str, artifact_location: str | None = None
    ) -> str:
        payload: dict[str, Any] = {"name": name}
        if artifact_location:
            payload["artifact_location"] = artifact_location
        resp = await self._request("POST", "/experiments/create", json=payload)
        return resp["experiment_id"]

    async def get_experiment_by_name(self, name: str) -> dict[str, Any]:
        resp = await self._request(
            "GET", "/experiments/get-by-name", params={"experiment_name": name}
        )
        return resp["experiment"]

    async def get_or_create_experiment(
        self, name: str, artifact_location: str | None = None
    ) -> str:
        try:
            return await self.create_experiment(name, artifact_location)
        except MlflowError as e:
            if getattr(e, "code", "") == "RESOURCE_ALREADY_EXISTS":
                exp = await self.get_experiment_by_name(name)
                return exp["experiment_id"]
            raise

    async def search_experiments(self, max_results: int = 100) -> list[dict[str, Any]]:
        resp = await self._request(
            "POST", "/experiments/search", json={"max_results": max_results}
        )
        return resp.get("experiments", [])

    # runs
    async def create_run(
        self,
        experiment_id: str,
        *,
        start_time_ms: int,
        tags: list[dict[str, str]] | None = None,
    ) -> str:
        """Create an MLflow run.

        ``start_time_ms`` is REQUIRED because the MLflow REST API defaults
        the field to 0 (Unix epoch) when omitted — unlike the Python SDK
        which auto-fills ``int(time.time() * 1000)``. Spec § 4.2.
        """
        payload: dict[str, Any] = {
            "experiment_id": experiment_id,
            "start_time": start_time_ms,
        }
        if tags:
            payload["tags"] = tags
        resp = await self._request("POST", "/runs/create", json=payload)
        return resp["run"]["info"]["run_id"]

    async def get_run(self, run_id: str) -> dict[str, Any]:
        resp = await self._request("GET", "/runs/get", params={"run_id": run_id})
        return resp["run"]

    async def search_runs(
        self,
        experiment_ids: list[str],
        filter_string: str | None = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "experiment_ids": experiment_ids,
            "max_results": max_results,
        }
        if filter_string:
            payload["filter"] = filter_string
        resp = await self._request("POST", "/runs/search", json=payload)
        return resp.get("runs", [])

    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        end_time_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"run_id": run_id}
        if status:
            payload["status"] = status
        if end_time_ms:
            payload["end_time"] = end_time_ms
        await self._request("POST", "/runs/update", json=payload)

    async def set_experiment_tag(
        self, experiment_id: str, key: str, value: str
    ) -> None:
        """Set an experiment-level tag.

        ``mlflow.note.content`` is rendered as Markdown by the MLflow native
        UI on the experiment page header.
        """
        await self._request(
            "POST",
            "/experiments/set-experiment-tag",
            json={"experiment_id": experiment_id, "key": key, "value": value},
        )

    async def set_run_tag(self, run_id: str, key: str, value: str) -> None:
        await self._request(
            "POST", "/runs/set-tag", json={"run_id": run_id, "key": key, "value": value}
        )

    # model registry
    async def create_model_version(
        self, name: str, source: str, run_id: str
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/model-versions/create",
            json={"name": name, "source": source, "run_id": run_id},
        )
        return resp["model_version"]

    async def transition_model_version_stage(
        self,
        name: str,
        version: str,
        stage: str,
        archive_existing_versions: bool = False,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/model-versions/transition-stage",
            json={
                "name": name,
                "version": str(version),
                "stage": stage,
                "archive_existing_versions": archive_existing_versions,
            },
        )
        return resp["model_version"]

    async def delete_model_version(self, name: str, version: str) -> None:
        await self._request(
            "DELETE",
            "/model-versions/delete",
            json={"name": name, "version": str(version)},
        )

    async def search_registered_models(
        self, max_results: int = 100
    ) -> list[dict[str, Any]]:
        resp = await self._request(
            "GET",
            "/registered-models/search",
            params={"max_results": max_results},
        )
        return resp.get("registered_models", [])

    async def search_model_versions(
        self,
        filter_string: str | None = None,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        # MLflow 2.x exposes /model-versions/search as GET-only (POST returns 405).
        params: dict[str, Any] = {"max_results": max_results}
        if filter_string:
            params["filter"] = filter_string
        resp = await self._request("GET", "/model-versions/search", params=params)
        return resp.get("model_versions", [])

    async def create_registered_model(self, name: str) -> dict[str, Any]:
        try:
            resp = await self._request(
                "POST", "/registered-models/create", json={"name": name}
            )
            return resp["registered_model"]
        except MlflowError as e:
            if getattr(e, "code", "") == "RESOURCE_ALREADY_EXISTS":
                return {"name": name}
            raise

    async def rename_registered_model(self, name: str, new_name: str) -> dict[str, Any]:
        """MLflow Model Registry rename — used by owner-transfer (T12).

        POST /api/2.0/mlflow/registered-models/rename
        """
        resp = await self._request(
            "POST",
            "/registered-models/rename",
            json={"name": name, "new_name": new_name},
        )
        return resp["registered_model"]

    async def delete_registered_model(self, name: str) -> None:
        """MLflow cascade-delete — used by DELETE /models/{owner}/{name} (T13).

        DELETE /api/2.0/mlflow/registered-models/delete
        """
        await self._request(
            "DELETE",
            "/registered-models/delete",
            json={"name": name},
        )
