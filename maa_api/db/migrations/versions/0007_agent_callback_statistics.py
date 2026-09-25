"""Persist structured stage drops and sanity observations."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007_agent_callback_statistics"
down_revision: Union[str, Sequence[str], None] = "0006_api_snippet_updated_at_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stage_drop",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("callback_id", sa.String(length=36), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("stage_code", sa.String(length=24), nullable=True),
        sa.Column("stars", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.String(length=64), nullable=True),
        sa.Column("item_name", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("add_quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_stage_drop_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_stage_drop_task_id_task",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stage_drop"),
    )
    op.create_index(
        "ix_stage_drop_stage_created_at",
        "stage_drop",
        ["stage_code", "created_at"],
    )
    op.create_index("ix_stage_drop_callback_id", "stage_drop", ["callback_id"])

    op.create_table(
        "sanity_observation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("stage_code", sa.String(length=24), nullable=True),
        sa.Column("current_sanity", sa.Integer(), nullable=False),
        sa.Column("max_sanity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_sanity_observation_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_sanity_observation_task_id_task",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sanity_observation"),
    )
    op.create_index(
        "ix_sanity_observation_stage_created_at",
        "sanity_observation",
        ["stage_code", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sanity_observation_stage_created_at", table_name="sanity_observation"
    )
    op.drop_table("sanity_observation")
    op.drop_index("ix_stage_drop_callback_id", table_name="stage_drop")
    op.drop_index("ix_stage_drop_stage_created_at", table_name="stage_drop")
    op.drop_table("stage_drop")
