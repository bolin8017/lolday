"""add audit_log table

Revision ID: 90125ce5ad35
Revises: 1afdf61e18f9
Create Date: 2026-05-14 14:18:38.078646

Spec: docs/superpowers/specs/2026-05-12-security-hardening-design.md §6.5
Finding: M-audit-log

Append-only audit trail for admin role-change, dataset.delete,
detector.delete (3 spec-listed sites, per plan design decision D2).
before_jsonb / after_jsonb are cherry-picked per call-site (D1) —
small dicts, not full ORM row dumps.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "90125ce5ad35"
down_revision: str | Sequence[str] | None = "1afdf61e18f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column(
            "before_jsonb",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "after_jsonb",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_log_actor_ts", "audit_log", ["actor_id", "ts"], unique=False
    )
    op.create_index(
        "ix_audit_log_target_ts",
        "audit_log",
        ["target_type", "target_id", "ts"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_log_target_ts", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_ts", table_name="audit_log")
    op.drop_table("audit_log")
