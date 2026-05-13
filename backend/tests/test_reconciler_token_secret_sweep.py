"""Tests for reconcile_orphan_token_secrets — sweeps job-token-* Secrets
whose vcjob was force-deleted (--grace-period=0 skips GC)."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest


def _secret(name: str, age_seconds: int) -> dict:
    created = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    return {
        "metadata": {
            "name": name,
            "namespace": "lolday-jobs",
            "creationTimestamp": created.replace("+00:00", "Z"),
        }
    }


class _CoreStub:
    def __init__(self, secrets: list[dict], deleted_record: list[str]):
        self._secrets = secrets
        self.deleted = deleted_record

    def list_namespaced_secret(self, namespace, **kw):
        class _R:
            pass

        r = _R()
        r.items = self._secrets
        return r

    def delete_namespaced_secret(self, name, namespace, **kw):
        self.deleted.append(name)


class _VolcanoStub:
    def __init__(self, items: list[dict]):
        self._items = items

    def list_namespaced_custom_object(self, group, version, namespace, plural, **kw):
        return {"items": self._items}


@pytest.mark.asyncio
async def test_sweep_deletes_old_orphan_token_secrets(db_session):
    """Secret older than JOB_TTL_SECONDS_AFTER_FINISHED + no matching vcjob
    → deleted."""
    from app.reconciler.orphans import reconcile_orphan_token_secrets

    deleted: list[str] = []
    secrets = [
        _secret(f"job-token-{uuid.uuid4().hex[:16]}", age_seconds=7 * 86400 + 60),
    ]
    core = _CoreStub(secrets, deleted)
    volcano = _VolcanoStub([])

    with (
        patch("app.reconciler.orphans.core_v1", return_value=core),
        patch("app.reconciler.orphans.volcano_v1alpha1", return_value=volcano),
    ):
        n = await reconcile_orphan_token_secrets(db_session)

    assert n == 1
    assert deleted == [secrets[0]["metadata"]["name"]]


@pytest.mark.asyncio
async def test_sweep_keeps_young_secrets(db_session):
    """A Secret younger than the TTL must NOT be deleted — the parent vcjob
    may still be running and the GC hasn't fired yet."""
    from app.reconciler.orphans import reconcile_orphan_token_secrets

    deleted: list[str] = []
    young = _secret(f"job-token-{uuid.uuid4().hex[:16]}", age_seconds=60)
    core = _CoreStub([young], deleted)
    volcano = _VolcanoStub([])

    with (
        patch("app.reconciler.orphans.core_v1", return_value=core),
        patch("app.reconciler.orphans.volcano_v1alpha1", return_value=volcano),
    ):
        n = await reconcile_orphan_token_secrets(db_session)

    assert n == 0
    assert deleted == []


@pytest.mark.asyncio
async def test_sweep_keeps_secrets_with_live_vcjob(db_session):
    """A Secret whose name encodes a job-id matching a live vcjob must be
    kept, even if it's old. (Live vcjob → ownerRef GC will handle it on
    eventual deletion.)"""
    from app.reconciler.orphans import reconcile_orphan_token_secrets

    job_short = uuid.uuid4().hex[:16]
    secret_name = f"job-token-{job_short}"
    deleted: list[str] = []
    old = _secret(secret_name, age_seconds=7 * 86400 + 60)
    live_vcjob = {
        "metadata": {
            "name": f"job-train-{job_short}",
            "labels": {"lolday.job-id": str(uuid.UUID(job_short.ljust(32, "0")))},
        }
    }
    core = _CoreStub([old], deleted)
    volcano = _VolcanoStub([live_vcjob])

    with (
        patch("app.reconciler.orphans.core_v1", return_value=core),
        patch("app.reconciler.orphans.volcano_v1alpha1", return_value=volcano),
    ):
        # NOTE: the implementation correlates by job-short-id matching the
        # vcjob's lolday.job-id label prefix; if your implementation uses a
        # different matching key, adjust this fixture accordingly.
        n = await reconcile_orphan_token_secrets(db_session)

    assert n == 0
    assert deleted == []
