"""phase model namespace and visibility

Add User.handle; create registered_model, model_visibility_log,
model_owner_transfer_log; refactor model_version (drop mlflow_name,
add registered_model_id FK + visibility).

Pre-condition: model_version and model_transition_log are empty when this
migration runs (operator wipes pre-deploy per Phase C Task 29). The upgrade()
guard at step 4 aborts loudly with RuntimeError if model_version has rows.

Revision ID: 268e0765531f
Revises: 8871984f6fda
Create Date: 2026-05-07 11:04:27.359098

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "268e0765531f"
down_revision = "8871984f6fda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. user.handle ----
    # Add as nullable first so the column can exist before backfill.
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(
            sa.Column("handle", sa.String(60), nullable=True),
        )

    # Backfill: derive + collision-resolve handle for every existing user.
    from app.services.user_handle import (  # deferred: keeps migration importable without full app context at autogen time
        derive_handle_from_email,
        next_unique_handle,
    )

    rows = bind.execute(
        sa.text('SELECT id, email FROM "user" ORDER BY created_at')
    ).all()
    used: set[str] = set()
    for row in rows:
        base = derive_handle_from_email(row.email)
        handle = next_unique_handle(base, existing=used)
        used.add(handle)
        bind.execute(
            sa.text('UPDATE "user" SET handle = :h WHERE id = :id'),
            {"h": handle, "id": row.id},
        )

    # Now tighten to NOT NULL + unique index.
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column("handle", nullable=False)
    op.create_index("ix_user_handle", "user", ["handle"], unique=True)

    # ---- 2. New enum type (PostgreSQL only; SQLite stores enums as plain strings) ----
    # Use a DO block so a leftover type from a previous failed migration run
    # doesn't block re-execution. PostgreSQL has no `CREATE TYPE IF NOT
    # EXISTS`; this is the standard idempotent idiom.
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                DO $$ BEGIN
                    CREATE TYPE model_version_visibility_enum AS ENUM ('public', 'private');
                EXCEPTION
                    WHEN duplicate_object THEN null;
                END $$;
                """
            )
        )

    # ---- 3. registered_model table ----
    _tags_default = (
        sa.text("'{}'::jsonb")
        if bind.dialect.name == "postgresql"
        else sa.text("'{}'")
    )
    op.create_table(
        "registered_model",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "detector_id",
            sa.Uuid(),
            sa.ForeignKey("detector.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=_tags_default,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.UniqueConstraint(
            "owner_id", "detector_id", name="uq_registered_model_owner_detector"
        ),
    )
    op.create_index(
        "ix_registered_model_owner", "registered_model", ["owner_id"]
    )
    op.create_index(
        "ix_registered_model_detector", "registered_model", ["detector_id"]
    )

    # ---- 4. Refactor model_version ----
    # Pre-condition: table must be empty (operator wipes pre-deploy per spec §4.3).
    row_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM model_version")
    ).scalar()
    if row_count and row_count > 0:
        raise RuntimeError(
            f"model_version has {row_count} rows; this migration requires "
            "an empty table. Run pre-deploy wipe (see spec §4.3)."
        )

    # Drop existing unique index — it references mlflow_name which we are dropping.
    op.drop_index("ix_model_version_name_version_unique", table_name="model_version")

    # SQLite requires batch mode for drop_column / add_column / alter_column.
    # naming_convention is required so alembic can name the anonymous FK constraints
    # that the baseline migration created on model_version without explicit names
    # (SQLite does not enforce FK names; alembic batch rebuild needs them).
    _nc = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ix": "ix_%(table_name)s_%(column_0_label)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    }
    with op.batch_alter_table("model_version", naming_convention=_nc) as batch_op:
        batch_op.drop_column("mlflow_name")
        batch_op.add_column(
            sa.Column(
                "registered_model_id",
                sa.Uuid(),
                nullable=False,
            ),
        )
        batch_op.create_foreign_key(
            "fk_model_version_registered_model_id_registered_model",
            "registered_model",
            ["registered_model_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.Enum(
                    "public",
                    "private",
                    name="model_version_visibility_enum",
                    create_type=False,
                ),
                nullable=False,
                server_default="private",
            ),
        )
        # Remove server_default now that the column is populated.
        batch_op.alter_column(
            "visibility",
            server_default=None,
        )
        batch_op.create_unique_constraint(
            "uq_model_version_per_registered",
            ["registered_model_id", "mlflow_version"],
        )

    op.create_index(
        "ix_model_version_registered_model",
        "model_version",
        ["registered_model_id"],
    )
    op.create_index(
        "ix_model_version_visibility", "model_version", ["visibility"]
    )

    # ---- 5. Audit log tables ----
    # On PostgreSQL, alembic's `op.create_table()` with sa.Enum columns
    # forces a CREATE TYPE even with create_type=False, so we use raw SQL
    # for the visibility-log table. SQLite path uses op.create_table because
    # SQLite stores enums as VARCHAR with CHECK and never tries CREATE TYPE.
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                CREATE TABLE model_visibility_log (
                    id UUID PRIMARY KEY,
                    model_version_id UUID NOT NULL REFERENCES model_version(id) ON DELETE CASCADE,
                    from_visibility model_version_visibility_enum NOT NULL,
                    to_visibility model_version_visibility_enum NOT NULL,
                    actor_id UUID NOT NULL REFERENCES "user"(id),
                    comment TEXT,
                    changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    else:
        op.create_table(
            "model_visibility_log",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "model_version_id",
                sa.Uuid(),
                sa.ForeignKey("model_version.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "from_visibility",
                sa.Enum(
                    "public",
                    "private",
                    name="model_version_visibility_enum",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "to_visibility",
                sa.Enum(
                    "public",
                    "private",
                    name="model_version_visibility_enum",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "actor_id",
                sa.Uuid(),
                sa.ForeignKey("user.id"),
                nullable=False,
            ),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column(
                "changed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )
    op.create_index(
        "ix_model_visibility_log_version",
        "model_visibility_log",
        ["model_version_id"],
    )

    op.create_table(
        "model_owner_transfer_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "registered_model_id",
            sa.Uuid(),
            sa.ForeignKey("registered_model.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column(
            "to_owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "transferred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )
    op.create_index(
        "ix_model_owner_transfer_log_model",
        "model_owner_transfer_log",
        ["registered_model_id"],
    )


def downgrade() -> None:
    """Local-dev rollback only — repo policy forbids prod downgrades
    (.claude/rules/alembic-migrations.md). model_version.mlflow_name
    restored as NOT NULL because the column had that constraint before;
    callers must backfill before rolling forward again."""
    bind = op.get_bind()

    op.drop_index(
        "ix_model_owner_transfer_log_model", table_name="model_owner_transfer_log"
    )
    op.drop_table("model_owner_transfer_log")

    op.drop_index(
        "ix_model_visibility_log_version", table_name="model_visibility_log"
    )
    op.drop_table("model_visibility_log")

    op.drop_index("ix_model_version_visibility", table_name="model_version")
    op.drop_index(
        "ix_model_version_registered_model", table_name="model_version"
    )

    _nc = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ix": "ix_%(table_name)s_%(column_0_label)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    }
    with op.batch_alter_table("model_version", naming_convention=_nc) as batch_op:
        batch_op.drop_constraint("uq_model_version_per_registered", type_="unique")
        batch_op.drop_constraint(
            "fk_model_version_registered_model_id_registered_model", type_="foreignkey"
        )
        batch_op.drop_column("visibility")
        batch_op.drop_column("registered_model_id")
        batch_op.add_column(
            sa.Column(
                "mlflow_name",
                sa.String(200),
                nullable=False,
                server_default=sa.text("'__rollback_unknown__'"),
            ),
        )
        # Drop server_default immediately — it was only needed to satisfy NOT NULL
        # for any existing rows during the ADD COLUMN DDL (2-step pattern per
        # .claude/rules/alembic-migrations.md).
        batch_op.alter_column("mlflow_name", server_default=None)

    op.create_index(
        "ix_model_version_name_version_unique",
        "model_version",
        ["mlflow_name", "mlflow_version"],
        unique=True,
    )

    op.drop_index("ix_registered_model_detector", table_name="registered_model")
    op.drop_index("ix_registered_model_owner", table_name="registered_model")
    op.drop_table("registered_model")

    if bind.dialect.name == "postgresql":
        sa.Enum(name="model_version_visibility_enum").drop(
            bind, checkfirst=False
        )

    op.drop_index("ix_user_handle", table_name="user")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("handle")
