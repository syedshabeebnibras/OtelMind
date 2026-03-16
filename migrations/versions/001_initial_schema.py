"""Initial schema — all OtelMind tables.

Revision ID: 001
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- traces ---
    op.create_table(
        "traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", sa.String(64), unique=True, nullable=False),
        sa.Column("service_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ok"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_traces_trace_id", "traces", ["trace_id"])
    op.create_index("ix_traces_service_name", "traces", ["service_name"])
    op.create_index("ix_traces_start_time", "traces", ["start_time"])

    # --- spans ---
    op.create_table(
        "spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("span_id", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "trace_id",
            sa.String(64),
            sa.ForeignKey("traces.trace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_span_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="INTERNAL"),
        sa.Column("status_code", sa.String(32), nullable=False, server_default="OK"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float, nullable=True),
        sa.Column("attributes", postgresql.JSONB, nullable=True),
        sa.Column("events", postgresql.JSONB, nullable=True),
        sa.Column("inputs", postgresql.JSONB, nullable=True),
        sa.Column("outputs", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_spans_span_id", "spans", ["span_id"])
    op.create_index("ix_spans_trace_id", "spans", ["trace_id"])
    op.create_index("ix_spans_name", "spans", ["name"])
    op.create_index("ix_spans_start_time", "spans", ["start_time"])

    # --- token_counts ---
    op.create_table(
        "token_counts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "trace_id",
            sa.String(64),
            sa.ForeignKey("traces.trace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_token_counts_trace_id", "token_counts", ["trace_id"])

    # --- tool_errors ---
    op.create_table(
        "tool_errors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "span_id",
            sa.String(64),
            sa.ForeignKey("spans.span_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("stack_trace", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_tool_errors_span_id", "tool_errors", ["span_id"])

    # --- failure_classifications ---
    op.create_table(
        "failure_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "trace_id",
            sa.String(64),
            sa.ForeignKey("traces.trace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("failure_type", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=True),
        sa.Column(
            "detection_method",
            sa.String(64),
            nullable=False,
            server_default="heuristic",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_failure_class_trace_id", "failure_classifications", ["trace_id"])
    op.create_index("ix_failure_class_type", "failure_classifications", ["failure_type"])

    # --- remediation_actions ---
    op.create_table(
        "remediation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "failure_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("failure_classifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_remediation_trace_id", "remediation_actions", ["trace_id"])
    op.create_index("ix_remediation_status", "remediation_actions", ["status"])


def downgrade() -> None:
    op.drop_table("remediation_actions")
    op.drop_table("failure_classifications")
    op.drop_table("tool_errors")
    op.drop_table("token_counts")
    op.drop_table("spans")
    op.drop_table("traces")
