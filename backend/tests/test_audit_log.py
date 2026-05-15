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


# ---------------------------------------------------------------------------
# #166 — audit-log expansion. Coverage for the new call sites.
# ---------------------------------------------------------------------------


async def test_audit_log_written_on_credential_upsert(user_client, db_session):
    """PUT /me/git-credential writes audit row with provider + token_hint."""
    from app.models import AuditLog, User

    resp = await user_client.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_" + "Z" * 36},
    )
    assert resp.status_code == 200, resp.text

    owner = (
        await db_session.execute(select(User).where(User.email == "user1@example.dev"))
    ).scalar_one()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == owner.id,
                    AuditLog.action == "credential.upsert",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "git_credential"
    # The audit row stores the hint, not the cleartext PAT body. token_hint
    # is the masked 'ghp_...XXXX' shape so the prefix is expected; the full
    # PAT body (36 chars after ghp_) must NOT be present.
    assert "token_hint" in rows[0].after_jsonb
    full_pat = "ghp_" + "Z" * 36
    assert full_pat not in str(rows[0].after_jsonb)


async def test_audit_log_written_on_credential_delete(user_client, db_session):
    """DELETE /me/git-credential writes audit row referencing the wiped credential."""
    from app.models import AuditLog, User

    # Pre-seed a credential.
    await user_client.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_" + "Y" * 36},
    )
    del_resp = await user_client.delete("/api/v1/users/me/git-credential")
    assert del_resp.status_code == 204

    owner = (
        await db_session.execute(select(User).where(User.email == "user1@example.dev"))
    ).scalar_one()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == owner.id,
                    AuditLog.action == "credential.delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "git_credential"
    assert "token_hint" in rows[0].before_jsonb


async def test_audit_log_written_on_dataset_visibility_change(user_client, db_session):
    """PATCH /datasets/{id} with visibility flip writes audit row with before/after."""
    from app.models import AuditLog

    ds_id = await _seed_dataset_for_user_client(db_session, user_client)

    # Default create visibility is PUBLIC; flip to PRIVATE.
    resp = await user_client.patch(
        f"/api/v1/datasets/{ds_id}",
        json={"visibility": "private"},
    )
    assert resp.status_code == 200, resp.text

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == ds_id,
                    AuditLog.action == "dataset.visibility",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "dataset"
    assert rows[0].before_jsonb == {"visibility": "public"}
    assert rows[0].after_jsonb == {"visibility": "private"}


async def test_audit_log_not_written_on_dataset_visibility_no_op(
    user_client, db_session
):
    """PATCH with same visibility must NOT write an audit row."""
    from app.models import AuditLog

    ds_id = await _seed_dataset_for_user_client(db_session, user_client)
    # Default create is public; patching to public is a no-op.
    resp = await user_client.patch(
        f"/api/v1/datasets/{ds_id}",
        json={"visibility": "public"},
    )
    assert resp.status_code == 200

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == ds_id,
                    AuditLog.action == "dataset.visibility",
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_audit_log_written_on_auth_login_first_resolve(db_session):
    """First-time CF Access principal resolution writes an auth.login audit row."""
    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import AuditLog

    email = "audit-login-probe@example.dev"
    user = await get_or_create_user_by_email(db_session, email)

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == user.id,
                    AuditLog.action == "auth.login",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "user"
    assert rows[0].after_jsonb["email"] == email

    # Second resolution should NOT write a new row (existing principal).
    await get_or_create_user_by_email(db_session, email)
    rows2 = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == user.id,
                    AuditLog.action == "auth.login",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows2) == 1


async def test_audit_log_written_on_detector_register(user_client, db_session):
    """POST /detectors writes audit row with repo URL but NOT the PAT."""
    from app.models import AuditLog, Role, User

    # Need DEVELOPER for /detectors POST.
    owner = (
        await db_session.execute(select(User).where(User.email == "user1@example.dev"))
    ).scalar_one()
    owner.role = Role.DEVELOPER
    await db_session.commit()

    from app.routers import detectors as dr

    async def fake_meta(url, pat):
        return {"name": "audit-reg-det", "description": "x", "display_name": "x"}

    # Patch via monkeypatch-style attribute setattr.
    orig = dr._clone_and_validate
    dr._clone_and_validate = fake_meta
    try:
        # Set credential first.
        await user_client.put(
            "/api/v1/users/me/git-credential",
            json={"provider": "github", "token": "ghp_" + "Q" * 36},
        )

        resp = await user_client.post(
            "/api/v1/detectors",
            json={"git_url": "https://github.com/test/audit-reg-det.git"},
        )
        assert resp.status_code == 201, resp.text
        det_id = uuid.UUID(resp.json()["id"])
    finally:
        dr._clone_and_validate = orig

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == det_id,
                    AuditLog.action == "detector.register",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "detector"
    assert "git_url" in rows[0].after_jsonb
    # PAT must NOT appear in the audit row.
    assert "ghp_" not in str(rows[0].after_jsonb)
