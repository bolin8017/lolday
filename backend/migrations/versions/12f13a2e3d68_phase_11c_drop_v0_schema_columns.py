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
    validation. No data preserved on downgrade — compat is not a goal.
    """
    op.drop_column("detector_build", "pending_schema")
    op.drop_column("detector_version", "config_schema")


def downgrade() -> None:
    """Re-add the columns as nullable empty JSON. No row-level data is restored."""
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
