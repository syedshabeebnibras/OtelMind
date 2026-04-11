"""Trace scores table for continuous auto-evaluation.

Revision ID: 006
Revises: 005
Create Date: 2026-04-11

Populated by the background auto-scoring loop. Each row is one
dimensional LLM-judge score (`faithfulness`, `relevance`, `coherence`,
`safety`, `tool_use_accuracy`) for a sampled trace.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("raw_score", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("method", sa.String(32), nullable=False, server_default="llm"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_trace_scores_tenant_created",
        "trace_scores",
        ["tenant_id", "created_at"],
    )
    op.create_index("ix_trace_scores_trace_id", "trace_scores", ["trace_id"])
    op.create_index(
        "ix_trace_scores_dimension",
        "trace_scores",
        ["tenant_id", "dimension", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trace_scores_dimension", table_name="trace_scores")
    op.drop_index("ix_trace_scores_trace_id", table_name="trace_scores")
    op.drop_index("ix_trace_scores_tenant_created", table_name="trace_scores")
    op.drop_table("trace_scores")
