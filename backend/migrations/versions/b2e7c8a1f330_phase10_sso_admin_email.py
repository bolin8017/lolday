"""phase10_sso_admin_email

Rename the seeded admin account's email from the legacy
``admin@lolday.dev`` placeholder to the real operator's GitHub primary
email so that the first SSO login maps onto the existing row (preserving
``owner_id`` on every detector/job/dataset created pre-switch) instead of
creating a new User and orphaning legacy data.

The replacement email is read from the ``SSO_ADMIN_EMAIL`` environment
variable (set via helm values). If unset, the migration is a no-op — safe
to apply before the operator is ready.

Revision ID: b2e7c8a1f330
Revises: 8a1c2d4e5f60
Create Date: 2026-04-22 13:00:00.000000
"""
from __future__ import annotations

import os
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "b2e7c8a1f330"
down_revision: Union[str, Sequence[str], None] = "8a1c2d4e5f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    new_email = os.environ.get("SSO_ADMIN_EMAIL", "").strip()
    if not new_email:
        return  # helm values not populated yet; re-run will pick it up

    conn = op.get_bind()
    conn.execute(
        text('UPDATE "user" SET email = :new WHERE email = :old'),
        {"new": new_email, "old": "admin@lolday.dev"},
    )


def downgrade() -> None:
    # Downgrade would require re-inferring the original admin email, which
    # is inherently lossy. If roll-back is needed, restore from backup.
    pass
