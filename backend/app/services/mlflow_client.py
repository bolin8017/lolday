import asyncio
import logging
from typing import Any

import httpx

from app.metrics import BACKEND_ERRORS

logger = logging.getLogger(__name__)


class MlflowError(Exception):
    pass


# Module-level shared httpx.AsyncClient, lazy-initialized on first use.
# Mirrors gpu_signal's module-level Client pattern (spec
# 2026-05-12-backend-httpx-client-leak-fix-design.md) — `async with
# httpx.AsyncClient(...)` per `_request` call churned ~0.8 MiB of glibc
# arena pages per construction, and sync_model_versions runs every 60 s,
# contributing the ~0.9 MiB/min residual observed in v0.21.1 production
# (spec 2026-05-12-mlflow-client-async-leak-fix-design.md §2).
#
# Lazy (not eager) because httpx.AsyncClient.__init__ binds anyio
# task-group machinery to the current event loop and module import
# happens before the FastAPI loop starts.  The lock prevents two
# concurrent first-time callers from racing into separate Clients.
_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_CLIENT_LOCK = asyncio.Lock()


async def _get_http_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it lazily under a lock.

    First caller's ``timeout`` becomes the Client's default; per-request
    overrides are still available via ``client.request(..., timeout=...)``
    at the call site.  All real callers in this codebase pass the same
    default 10 s.
    """
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        async with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.AsyncClient(timeout=timeout)
    return _HTTP_CLIENT


async def close_http_client() -> None:
    """Close the shared MLflow AsyncClient. Called from FastAPI lifespan teardown.

    Idempotent — safe to call when the Client is already closed or was
    never constructed.  Reference is cleared *before* ``aclose()`` runs
    so even on transport failure the post-close invariant holds and
    subsequent ``_request`` calls re-create a fresh Client through
    ``_get_http_client``.  ``aclose()``-side exceptions are logged +
    counted via ``BACKEND_ERRORS{stage='mlflow_client_close'}`` but
    never re-raised — lifespan teardown is best-effort hygiene.
    """
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    client = _HTTP_CLIENT
    _HTTP_CLIENT = None
    try:
        await client.aclose()
    except Exception:
        BACKEND_ERRORS.labels(stage="mlflow_client_close").inc()
        logger.exception("mlflow_client: AsyncClient.aclose() raised during shutdown")


class MlflowClient:
    """Async thin REST wrapper for MLflow Tracking + Model Registry.

    We don't import mlflow-skinny's own client because it's sync; we reuse httpx
    for backend-wide async consistency. Endpoints per MLflow 2.20 REST API.
    """

    def __init__(
        self,
        tracking_uri: str,
        timeout: float = 10.0,
        retries: int = 3,
    ) -> None:
        self._base = tracking_uri.rstrip("/")
        self._timeout = httpx.Timeout(timeout)
        self._retries = retries

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}/api/2.0/mlflow{path}"
        client = await _get_http_client(self._timeout)
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = await client.request(method, url, json=json, params=params)
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
