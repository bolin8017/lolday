"""add phase 4 tables (dataset_config, job, model_version, model_transition_log) + detector_version.mlflow_experiment_id

Revision ID: a1b2c3d4
Revises: c13efbf4
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'c13efbf4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Phase 4 tables and extend detector_version."""
    bind = op.get_bind()

    # --- ENUMs (PostgreSQL only) ---
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE TYPE dataset_visibility_enum AS ENUM ('public', 'private')"
        )
        op.execute(
            "CREATE TYPE job_type_enum AS ENUM ('train', 'evaluate', 'predict')"
        )
        op.execute(
            "CREATE TYPE job_status_enum AS ENUM "
            "('pending', 'preparing', 'running', 'succeeded', 'failed', 'cancelled', 'timeout')"
        )
        op.execute(
            "CREATE TYPE resource_profile_enum AS ENUM ('standard')"
        )
        op.execute(
            "CREATE TYPE model_stage_enum AS ENUM ('None', 'Staging', 'Production', 'Archived')"
        )

    # --- dataset_config ---
    op.create_table(
        'dataset_config',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column(
            'visibility',
            sa.Enum('public', 'private', name='dataset_visibility_enum',
                    create_type=False),
            nullable=False,
        ),
        sa.Column('csv_content', sa.Text(), nullable=False),
        sa.Column('csv_checksum', sa.String(length=64), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.Column(
            'label_distribution',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column('family_distribution', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
                  nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                  nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dataset_config_owner', 'dataset_config', ['owner_id'])
    op.create_index('ix_dataset_config_visibility', 'dataset_config', ['visibility'])
    if bind.dialect.name == "postgresql":
        op.create_index(
            'ix_dataset_config_owner_name_unique',
            'dataset_config',
            ['owner_id', 'name'],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )
    else:
        op.create_index(
            'ix_dataset_config_owner_name_unique',
            'dataset_config',
            ['owner_id', 'name'],
            unique=True,
        )

    # --- job (without circular FK to model_version yet) ---
    op.create_table(
        'job',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'type',
            sa.Enum('train', 'evaluate', 'predict', name='job_type_enum',
                    create_type=False),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum('pending', 'preparing', 'running', 'succeeded', 'failed',
                    'cancelled', 'timeout', name='job_status_enum',
                    create_type=False),
            nullable=False,
        ),
        sa.Column('detector_version_id', sa.UUID(), nullable=False),
        sa.Column('train_dataset_id', sa.UUID(), nullable=True),
        sa.Column('test_dataset_id', sa.UUID(), nullable=True),
        sa.Column('predict_dataset_id', sa.UUID(), nullable=True),
        # source_model_version_id column present but FK added later (circular ref)
        sa.Column('source_model_version_id', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('resolved_config', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
                  nullable=False),
        sa.Column('mlflow_experiment_id', sa.String(length=50), nullable=True),
        sa.Column('mlflow_run_id', sa.String(length=50), nullable=True),
        sa.Column('k8s_job_name', sa.String(length=100), nullable=True),
        sa.Column('failure_reason', sa.String(length=100), nullable=True),
        sa.Column('log_tail', sa.Text(), nullable=True),
        sa.Column('summary_metrics', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
                  nullable=True),
        sa.Column(
            'resource_profile',
            sa.Enum('standard', name='resource_profile_enum', create_type=False),
            nullable=False,
        ),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['detector_version_id'], ['detector_version.id']),
        sa.ForeignKeyConstraint(['train_dataset_id'], ['dataset_config.id']),
        sa.ForeignKeyConstraint(['test_dataset_id'], ['dataset_config.id']),
        sa.ForeignKeyConstraint(['predict_dataset_id'], ['dataset_config.id']),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_job_owner_submitted', 'job', ['owner_id', 'submitted_at'])
    op.create_index('ix_job_detector_version', 'job', ['detector_version_id'])
    op.create_index('ix_job_idempotency', 'job', ['idempotency_key', 'submitted_at'])
    if bind.dialect.name == "postgresql":
        op.create_index(
            'ix_job_in_flight',
            'job',
            ['status'],
            postgresql_where=sa.text(
                "status IN ('pending'::job_status_enum,"
                " 'preparing'::job_status_enum,"
                " 'running'::job_status_enum)"
            ),
        )
    else:
        op.create_index('ix_job_in_flight', 'job', ['status'])

    # --- model_version (source_job_id FK to job is valid now) ---
    op.create_table(
        'model_version',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('mlflow_name', sa.String(length=200), nullable=False),
        sa.Column('mlflow_version', sa.Integer(), nullable=False),
        sa.Column('mlflow_run_id', sa.String(length=50), nullable=False),
        sa.Column(
            'current_stage',
            sa.Enum('None', 'Staging', 'Production', 'Archived',
                    name='model_stage_enum', create_type=False),
            nullable=False,
        ),
        sa.Column('detector_version_id', sa.UUID(), nullable=False),
        sa.Column('source_job_id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('last_transitioned_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['detector_version_id'], ['detector_version.id']),
        sa.ForeignKeyConstraint(['source_job_id'], ['job.id']),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_model_version_name_version_unique',
        'model_version',
        ['mlflow_name', 'mlflow_version'],
        unique=True,
    )
    op.create_index('ix_model_version_owner', 'model_version', ['owner_id'])
    op.create_index('ix_model_version_stage', 'model_version', ['current_stage'])

    # --- model_transition_log ---
    op.create_table(
        'model_transition_log',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('model_version_id', sa.UUID(), nullable=False),
        sa.Column(
            'from_stage',
            sa.Enum('None', 'Staging', 'Production', 'Archived',
                    name='model_stage_enum', create_type=False),
            nullable=False,
        ),
        sa.Column(
            'to_stage',
            sa.Enum('None', 'Staging', 'Production', 'Archived',
                    name='model_stage_enum', create_type=False),
            nullable=False,
        ),
        sa.Column('actor_id', sa.UUID(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('transitioned_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['model_version_id'], ['model_version.id']),
        sa.ForeignKeyConstraint(['actor_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_model_transition_version', 'model_transition_log', ['model_version_id']
    )

    # --- Add circular FK: job.source_model_version_id -> model_version.id ---
    op.create_foreign_key(
        'fk_job_source_model_version',
        'job',
        'model_version',
        ['source_model_version_id'],
        ['id'],
    )

    # --- Extend detector_version ---
    op.add_column(
        'detector_version',
        sa.Column('mlflow_experiment_id', sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    """Remove Phase 4 tables and revert detector_version extension."""
    bind = op.get_bind()

    # Drop circular FK first
    op.drop_constraint('fk_job_source_model_version', 'job', type_='foreignkey')

    # detector_version extension
    op.drop_column('detector_version', 'mlflow_experiment_id')

    # model_transition_log
    op.drop_index('ix_model_transition_version', table_name='model_transition_log')
    op.drop_table('model_transition_log')

    # model_version (drop before job FK constraint is gone — source_job_id FK still valid)
    op.drop_index('ix_model_version_stage', table_name='model_version')
    op.drop_index('ix_model_version_owner', table_name='model_version')
    op.drop_index('ix_model_version_name_version_unique', table_name='model_version')
    op.drop_table('model_version')

    # job
    op.drop_index('ix_job_in_flight', table_name='job')
    op.drop_index('ix_job_idempotency', table_name='job')
    op.drop_index('ix_job_detector_version', table_name='job')
    op.drop_index('ix_job_owner_submitted', table_name='job')
    op.drop_table('job')

    # dataset_config
    op.drop_index('ix_dataset_config_owner_name_unique', table_name='dataset_config')
    op.drop_index('ix_dataset_config_visibility', table_name='dataset_config')
    op.drop_index('ix_dataset_config_owner', table_name='dataset_config')
    op.drop_table('dataset_config')

    # ENUMs (PostgreSQL only)
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS model_stage_enum")
        op.execute("DROP TYPE IF EXISTS resource_profile_enum")
        op.execute("DROP TYPE IF EXISTS job_status_enum")
        op.execute("DROP TYPE IF EXISTS job_type_enum")
        op.execute("DROP TYPE IF EXISTS dataset_visibility_enum")
