"""Multi-agent group eval + judge calibration tables.

Revision ID: 007
Revises: 006
Create Date: 2026-04-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rounds_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_group_runs_tenant", "group_runs", ["tenant_id"])
    op.create_index("ix_group_runs_status", "group_runs", ["status"])

    op.create_table(
        "group_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_id", sa.String(128), nullable=False),
        sa.Column("sender_role", sa.String(128), nullable=False),
        sa.Column("recipient_id", sa.String(128), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["group_run_id"], ["group_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_group_messages_run", "group_messages", ["group_run_id"])

    op.create_table(
        "judge_calibrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("judge_model", sa.String(128), nullable=False),
        sa.Column("cohens_kappa", sa.Float(), nullable=True),
        sa.Column("agreement_rate", sa.Float(), nullable=True),
        sa.Column("bias", sa.Float(), nullable=True),
        sa.Column("per_dimension", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("calibration_curve", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_calibrations_tenant", "judge_calibrations", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_calibrations_tenant", table_name="judge_calibrations")
    op.drop_table("judge_calibrations")
    op.drop_index("ix_group_messages_run", table_name="group_messages")
    op.drop_table("group_messages")
    op.drop_index("ix_group_runs_status", table_name="group_runs")
    op.drop_index("ix_group_runs_tenant", table_name="group_runs")
    op.drop_table("group_runs")
