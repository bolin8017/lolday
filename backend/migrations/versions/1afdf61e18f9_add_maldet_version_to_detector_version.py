"""add maldet_version to detector_version

Revision ID: 1afdf61e18f9
Revises: 268e0765531f
Create Date: 2026-05-11 11:24:43.677601

Spec: docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md §5.8.

Adds a nullable VARCHAR(16) ``maldet_version`` column on ``detector_version``.
The value is captured at build time from the parsed manifest's
``compat.min_maldet`` field (pragmatic v1 — see Plan B Task 6).
Existing rows are filled NULL; later builds populate the value naturally.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1afdf61e18f9"
down_revision: str | Sequence[str] | None = "268e0765531f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "detector_version",
        sa.Column("maldet_version", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("detector_version", "maldet_version")
