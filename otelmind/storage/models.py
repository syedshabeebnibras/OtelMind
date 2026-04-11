"""SQLAlchemy ORM models for all OtelMind tables."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from otelmind.db import Base

# ---------------------------------------------------------------------------
# Multi-tenancy / Auth
# ---------------------------------------------------------------------------


class Tenant(Base):
    """An isolated workspace — one per company/team."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    # Retention in days: free=7, pro=30, enterprise=365
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey", back_populates="tenant", cascade="all,delete"
    )
    alert_channels: Mapped[list[AlertChannel]] = relationship(
        "AlertChannel", back_populates="tenant", cascade="all,delete"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        "AlertRule", back_populates="tenant", cascade="all,delete"
    )


class ApiKey(Base):
    """API key for authenticating SDK/collector requests."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # first 8 chars for display
    scopes: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="api_keys")

    __table_args__ = (Index("ix_api_keys_tenant_id", "tenant_id"),)

    @staticmethod
    def generate_key(prefix: str = "om_") -> tuple[str, str]:
        """Return (raw_key, key_hash). Store only the hash."""
        import hashlib

        raw = prefix + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw.encode()).hexdigest()
        return raw, key_hash


class AuditLog(Base):
    """Immutable log of every authenticated API action — required for SOC 2."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g. "traces.list"
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_tenant_id", "tenant_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# RBAC — users, roles, user ↔ tenant ↔ role assignments
# ---------------------------------------------------------------------------


class User(Base):
    """A human user that can belong to one or more tenants via a role."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list[UserTenantRole]] = relationship(
        "UserTenantRole", back_populates="user", cascade="all,delete"
    )


class Role(Base):
    """A named bundle of permission strings.

    `permissions` holds a list of `<resource>:<action>` tokens — e.g.
    `traces:read`, `alerts:write`. A wildcard `*` grants everything and
    is reserved for the seeded `owner` role.
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    # System roles are seeded by migrations and cannot be edited by tenants.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    assignments: Mapped[list[UserTenantRole]] = relationship(
        "UserTenantRole", back_populates="role"
    )


class UserTenantRole(Base):
    """Associates a user with a tenant and grants them a role in that tenant.

    One user can be a `viewer` in tenant A and an `admin` in tenant B.
    Unique on (user_id, tenant_id) — you get exactly one role per workspace.
    """

    __tablename__ = "user_tenant_roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship("User", back_populates="memberships")
    role: Mapped[Role] = relationship("Role", back_populates="assignments")

    __table_args__ = (
        Index("ix_utr_tenant_id", "tenant_id"),
        Index("ix_utr_user_id", "user_id"),
    )


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------


class AlertChannel(Base):
    """A notification destination — Slack webhook, PagerDuty key, email address."""

    __tablename__ = "alert_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)  # slack|pagerduty|email
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # e.g. {"webhook_url": "..."} or {"routing_key": "..."} or {"to": ["..."]}
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="alert_channels")
    rules: Mapped[list[AlertRule]] = relationship(
        "AlertRule", back_populates="channel", cascade="all,delete"
    )


class AlertRule(Base):
    """A rule that fires an alert when a failure type exceeds a confidence threshold."""

    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_channels.id", ondelete="CASCADE"), nullable=False
    )
    failure_type: Mapped[str] = mapped_column(String(64), nullable=False)  # or "*" for all
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Dedup window in seconds — suppress duplicate alerts within this window
    dedup_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="alert_rules")
    channel: Mapped[AlertChannel] = relationship("AlertChannel", back_populates="rules")


# ---------------------------------------------------------------------------
# Core telemetry (tenant-scoped)
# ---------------------------------------------------------------------------


class Trace(Base):
    """Top-level trace representing one LangGraph workflow execution.

    Note: `traces` is range-partitioned (compound PK id+created_at), so
    SQLAlchemy relationships to spans/token_counts use explicit
    `primaryjoin` on `trace_id` with `foreign()` annotation. No SQL-level
    FK exists — integrity is enforced at the service layer.
    """

    __tablename__ = "traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    spans: Mapped[list[Span]] = relationship(
        "Span",
        primaryjoin="Trace.trace_id == foreign(Span.trace_id)",
        viewonly=True,
    )
    token_counts: Mapped[list[TokenCount]] = relationship(
        "TokenCount",
        primaryjoin="Trace.trace_id == foreign(TokenCount.trace_id)",
        viewonly=True,
    )

    __table_args__ = (
        Index("ix_traces_tenant_service", "tenant_id", "service_name"),
        Index("ix_traces_tenant_start_time", "tenant_id", "start_time"),
    )


class Span(Base):
    """Individual span within a trace — maps to one LangGraph node execution.

    `span_id` and `trace_id` are both soft references — the `spans` table
    is range-partitioned (PK id+created_at) and Postgres disallows unique
    constraints that don't include the partition key.
    """

    __tablename__ = "spans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="INTERNAL")
    status_code: Mapped[str] = mapped_column(String(32), nullable=False, default="OK")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    inputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    outputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_spans_tenant_trace_id", "tenant_id", "trace_id"),
        Index("ix_spans_name", "name"),
        Index("ix_spans_start_time", "start_time"),
    )


class TokenCount(Base):
    """Token usage record per trace or span — used for cost attribution.

    `trace_id` is a soft reference (see `Trace` / `Span` docstrings).
    """

    __tablename__ = "token_counts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_token_counts_tenant_id", "tenant_id"),
        Index("ix_token_counts_trace_id", "trace_id"),
        Index("ix_token_counts_model", "tenant_id", "model_name"),
    )


class ToolError(Base):
    """Error captured when a tool invocation within a span fails.

    `span_id` is a soft reference — `spans` is range-partitioned in 003
    so a SQL-level FK to `spans.span_id` isn't possible. Integrity is
    enforced at the service layer when errors are recorded.
    """

    __tablename__ = "tool_errors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    span_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_tool_errors_span_id", "span_id"),)


class FailureClassification(Base):
    """Watchdog-detected failure classification for a trace.

    `trace_id` is a soft reference — see `Trace` docstring.
    """

    __tablename__ = "failure_classifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detection_method: Mapped[str] = mapped_column(String(64), nullable=False, default="heuristic")
    alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_failure_class_tenant_type", "tenant_id", "failure_type"),
        Index("ix_failure_class_trace_id", "trace_id"),
        Index("ix_failure_class_created_at", "tenant_id", "created_at"),
    )


class EvalRun(Base):
    """Persisted result of an evaluation / regression run.

    Stores the aggregate scores plus the compared baseline/candidate labels
    so the dashboard can render a history of model/prompt changes. The full
    per-case detail lives in `details` JSON for one-click drill-down.
    """

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    baseline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # e.g. {"faithfulness": 0.91, "relevance": 0.85, "safety": 1.0}
    scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    regression_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    improvement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_eval_runs_tenant_created", "tenant_id", "created_at"),
    )


class RemediationAction(Base):
    """Automated remediation action taken in response to a failure.

    `failure_id` is a *soft* reference: `failure_classifications` is
    range-partitioned by `created_at` (compound PK), so a SQL-level FK
    would require both columns. We keep the lookup cheap via an index and
    enforce referential integrity at the service layer, matching the
    approach used for `trace_id` on spans/token_counts/failures.
    """

    __tablename__ = "remediation_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    failure_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_remediation_tenant_id", "tenant_id"),
        Index("ix_remediation_trace_id", "trace_id"),
        Index("ix_remediation_failure_id", "failure_id"),
        Index("ix_remediation_status", "status"),
    )
