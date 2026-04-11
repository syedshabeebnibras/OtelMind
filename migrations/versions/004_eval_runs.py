"""Eval runs table — persists regression comparisons for the dashboard.

Revision ID: 004
Revises: 003
Create Date: 2026-04-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("baseline", sa.String(255), nullable=True),
        sa.Column("candidate", sa.String(255), nullable=True),
        sa.Column("dataset", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("scores", postgresql.JSONB, nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("regression_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("improvement_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_eval_runs_tenant_created", "eval_runs", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_eval_runs_tenant_created", table_name="eval_runs")
    op.drop_table("eval_runs")
