"""Multi-tenancy, API keys, audit logs, alerting, tenant-scoped telemetry.

Revision ID: 002
Revises: 001
Create Date: 2026-04-11
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Deterministic default tenant for existing rows / local dev
DEFAULT_TENANT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "otelmind.default"))


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "alert_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_alert_channels_tenant_id", "alert_channels", ["tenant_id"])

    op.create_table(
        "alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("failure_type", sa.String(64), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("dedup_window_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["alert_channels.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_alert_rules_tenant_id", "alert_rules", ["tenant_id"])

    op.execute(
        f"""
        INSERT INTO tenants (id, name, slug, plan, retention_days, is_active)
        VALUES ('{DEFAULT_TENANT_ID}'::uuid, 'Default', 'default', 'free', 30, true)
        """
    )

    # --- traces.tenant_id ---
    op.add_column("traces", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(f"UPDATE traces SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid")
    op.alter_column("traces", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_traces_tenant_id", "traces", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_traces_tenant_id", "traces", ["tenant_id"])
    op.create_index("ix_traces_tenant_service", "traces", ["tenant_id", "service_name"])
    op.create_index("ix_traces_tenant_start_time", "traces", ["tenant_id", "start_time"])

    # --- spans.tenant_id ---
    op.add_column("spans", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE spans s
            SET tenant_id = t.tenant_id
            FROM traces t
            WHERE s.trace_id = t.trace_id
            """
        )
    )
    op.alter_column("spans", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_spans_tenant_id", "spans", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_spans_tenant_trace_id", "spans", ["tenant_id", "trace_id"])

    # --- token_counts: tenant_id + pricing columns ---
    op.add_column("token_counts", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "token_counts",
        sa.Column("model_provider", sa.String(64), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "token_counts",
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.execute(
        sa.text(
            """
            UPDATE token_counts tc
            SET tenant_id = t.tenant_id
            FROM traces t
            WHERE tc.trace_id = t.trace_id
            """
        )
    )
    op.alter_column("token_counts", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_token_counts_tenant_id",
        "token_counts",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_token_counts_tenant_id", "token_counts", ["tenant_id"])
    op.create_index("ix_token_counts_model", "token_counts", ["tenant_id", "model_name"])

    # --- failure_classifications.tenant_id ---
    op.add_column(
        "failure_classifications",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "failure_classifications",
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        sa.text(
            """
            UPDATE failure_classifications fc
            SET tenant_id = t.tenant_id
            FROM traces t
            WHERE fc.trace_id = t.trace_id
            """
        )
    )
    op.alter_column("failure_classifications", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_failure_class_tenant_id",
        "failure_classifications",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_failure_class_tenant_type",
        "failure_classifications",
        ["tenant_id", "failure_type"],
    )
    op.create_index(
        "ix_failure_class_created_at",
        "failure_classifications",
        ["tenant_id", "created_at"],
    )

    # --- remediation_actions.tenant_id ---
    op.add_column(
        "remediation_actions",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE remediation_actions ra
            SET tenant_id = t.tenant_id
            FROM traces t
            WHERE ra.trace_id = t.trace_id
            """
        )
    )
    op.alter_column("remediation_actions", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_remediation_tenant_id",
        "remediation_actions",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_remediation_tenant_id", "remediation_actions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_remediation_tenant_id", table_name="remediation_actions")
    op.drop_constraint("fk_remediation_tenant_id", "remediation_actions", type_="foreignkey")
    op.drop_column("remediation_actions", "tenant_id")

    op.drop_index("ix_failure_class_created_at", table_name="failure_classifications")
    op.drop_index("ix_failure_class_tenant_type", table_name="failure_classifications")
    op.drop_constraint("fk_failure_class_tenant_id", "failure_classifications", type_="foreignkey")
    op.drop_column("failure_classifications", "alerted")
    op.drop_column("failure_classifications", "tenant_id")

    op.drop_index("ix_token_counts_model", table_name="token_counts")
    op.drop_index("ix_token_counts_tenant_id", table_name="token_counts")
    op.drop_constraint("fk_token_counts_tenant_id", "token_counts", type_="foreignkey")
    op.drop_column("token_counts", "cost_usd")
    op.drop_column("token_counts", "model_provider")
    op.drop_column("token_counts", "tenant_id")

    op.drop_index("ix_spans_tenant_trace_id", table_name="spans")
    op.drop_constraint("fk_spans_tenant_id", "spans", type_="foreignkey")
    op.drop_column("spans", "tenant_id")

    op.drop_index("ix_traces_tenant_start_time", table_name="traces")
    op.drop_index("ix_traces_tenant_service", table_name="traces")
    op.drop_index("ix_traces_tenant_id", table_name="traces")
    op.drop_constraint("fk_traces_tenant_id", "traces", type_="foreignkey")
    op.drop_column("traces", "tenant_id")

    op.drop_table("alert_rules")
    op.drop_table("alert_channels")
    op.drop_table("audit_logs")
    op.drop_table("api_keys")
    op.drop_table("tenants")
