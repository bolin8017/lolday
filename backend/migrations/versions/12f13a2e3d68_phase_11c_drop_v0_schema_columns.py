"""phase 11c drop v0 schema columns

Revision ID: 12f13a2e3d68
Revises: 74c95d81f74e
Create Date: 2026-04-26 23:29:40.009115

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# revision identifiers — keep alembic's auto-generated values
revision: str = "12f13a2e3d68"
down_revision: Union[str, Sequence[str], None] = "74c95d81f74e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the v0 schema-related columns.

    The pydantic JSON schema flow (validate-init-container → /builds/{id}/schema
    → detector_build.pending_schema → detector_version.config_schema → jobs
    router jsonschema.validate) is replaced in Phase 11c by manifest-driven
    validation. The Phase 11c contract also removes the build-token round-trip
    that was used to authenticate the validator's schema-POST callback —
    detector_build.build_token is dropped here. No data preserved on
    downgrade — compat is not a goal.
    """
    op.drop_column("detector_build", "pending_schema")
    op.drop_column("detector_version", "config_schema")
    op.drop_column("detector_build", "build_token")


def downgrade() -> None:
    """Re-add the columns as nullable empty JSON / nullable string.

    No row-level data is restored. ``detector_version.config_schema`` is
    re-added as NOT NULL with ``'{}'::jsonb`` server_default so existing
    rows backfill an empty schema; the downgrade does NOT restore the v0
    per-detector pydantic JSON schemas that the upgrade dropped.
    ``detector_build.build_token`` re-added as nullable String(80); existing
    rows will have NULL — downgrade does not re-issue v0 build tokens.
    """
    op.add_column(
        "detector_build",
        sa.Column(
            "pending_schema",
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )
    op.add_column(
        "detector_version",
        sa.Column(
            "config_schema",
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("detector_version", "config_schema", server_default=None)
    op.add_column(
        "detector_build",
        sa.Column("build_token", sa.String(length=80), nullable=True),
    )
