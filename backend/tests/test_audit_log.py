"""AuditLog rows must be written on admin role change, dataset delete, detector delete."""

import uuid
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "sample_dataset.csv").read_text()


async def _seed_dataset_for_user_client(
    db_session: AsyncSession, user_client: AsyncClient
) -> uuid.UUID:
    """Seed a dataset owned by user_client via the public POST endpoint.

    Returns the new dataset's UUID. Uses the API rather than an ORM insert so
    that the row is committed through the same session.maker / engine pair the
    request handlers see — avoids the cross-session visibility traps that bite
    when the test holds a long-lived db_session.
    """
    resp = await user_client.post(
        "/api/v1/datasets",
        json={"name": "audit-target", "csv_content": FIXTURE_CSV},
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def _seed_detector_for_user_client(
    db_session: AsyncSession, user_client: AsyncClient
) -> uuid.UUID:
    """Seed a detector owned by user1@example.dev (the user behind user_client).

    Lifts the ORM-direct pattern from test_models_delete.py — skipping the
    git-clone register flow keeps the test fast and avoids monkeypatching.
    """
    from app.models import Detector, User

    owner = (
        await db_session.execute(select(User).where(User.email == "user1@example.dev"))
    ).scalar_one()
    det = Detector(
        name=f"audit-det-{uuid.uuid4().hex[:8]}",
        display_name="Audit Detector",
        git_url=f"https://github.com/test/audit-det-{uuid.uuid4().hex[:8]}.git",
        owner_id=owner.id,
    )
    db_session.add(det)
    await db_session.commit()
    await db_session.refresh(det)
    return det.id


async def test_audit_log_written_on_admin_role_change(auth_client_admin, db_session):
    """PATCH /admin/users/{id} with role-change body writes one audit_log row."""
    from app.models import AuditLog, Role, User

    target = User(
        id=uuid.uuid4(),
        email="bob@example.com",
        role=Role.USER,
        handle="bob",
        display_name="Bob",
    )
    db_session.add(target)
    await db_session.commit()

    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == target.id,
                    AuditLog.action == "admin.role_change",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].before_jsonb == {"role": "user"}
    assert rows[0].after_jsonb == {"role": "developer"}
    assert rows[0].target_type == "user"


async def test_audit_log_written_on_dataset_delete(user_client, db_session):
    """DELETE /datasets/{id} writes one audit_log row."""
    from app.models import AuditLog

    ds_id = await _seed_dataset_for_user_client(db_session, user_client)

    resp = await user_client.delete(f"/api/v1/datasets/{ds_id}")
    assert resp.status_code == 204

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == ds_id,
                    AuditLog.action == "dataset.delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "dataset"
    assert "name" in rows[0].before_jsonb
    assert "deleted_at" in rows[0].after_jsonb


async def test_audit_log_written_on_detector_delete(user_client, db_session):
    """DELETE /detectors/{id} writes one audit_log row."""
    from app.models import AuditLog

    det_id = await _seed_detector_for_user_client(db_session, user_client)

    resp = await user_client.delete(f"/api/v1/detectors/{det_id}")
    assert resp.status_code == 204

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == det_id,
                    AuditLog.action == "detector.delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "detector"
    assert "git_url" in rows[0].before_jsonb
    assert "deleted_at" in rows[0].after_jsonb


async def test_audit_log_not_written_on_no_op_patch(auth_client_admin, db_session):
    """PATCH /admin/users/{id} with empty body must NOT write an audit row."""
    from app.models import AuditLog, Role, User

    target = User(
        id=uuid.uuid4(),
        email="carol@example.com",
        role=Role.USER,
        handle="carol",
        display_name="Carol",
    )
    db_session.add(target)
    await db_session.commit()

    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{target.id}",
        json={},
    )
    assert resp.status_code == 200

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.target_id == target.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
