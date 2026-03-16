"""SQLAlchemy ORM models for all OtelMind tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from otelmind.db import Base


class Trace(Base):
    """Top-level trace representing one LangGraph workflow execution."""

    __tablename__ = "traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    spans: Mapped[list[Span]] = relationship("Span", back_populates="trace", cascade="all,delete")
    token_counts: Mapped[list[TokenCount]] = relationship(
        "TokenCount", back_populates="trace", cascade="all,delete"
    )

    __table_args__ = (
        Index("ix_traces_service_name", "service_name"),
        Index("ix_traces_start_time", "start_time"),
    )


class Span(Base):
    """Individual span within a trace — maps to one LangGraph node execution."""

    __tablename__ = "spans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    span_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False
    )
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

    trace: Mapped[Trace] = relationship("Trace", back_populates="spans")
    tool_errors: Mapped[list[ToolError]] = relationship(
        "ToolError", back_populates="span", cascade="all,delete"
    )

    __table_args__ = (
        Index("ix_spans_trace_id", "trace_id"),
        Index("ix_spans_name", "name"),
        Index("ix_spans_start_time", "start_time"),
    )


class TokenCount(Base):
    """Token usage record per trace or span."""

    __tablename__ = "token_counts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False
    )
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trace: Mapped[Trace] = relationship("Trace", back_populates="token_counts")

    __table_args__ = (Index("ix_token_counts_trace_id", "trace_id"),)


class ToolError(Base):
    """Error captured when a tool invocation within a span fails."""

    __tablename__ = "tool_errors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    span_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("spans.span_id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    span: Mapped[Span] = relationship("Span", back_populates="tool_errors")

    __table_args__ = (Index("ix_tool_errors_span_id", "span_id"),)


class FailureClassification(Base):
    """Watchdog-detected failure classification for a trace."""

    __tablename__ = "failure_classifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False
    )
    failure_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # hallucination, tool_timeout, infinite_loop, tool_misuse, context_overflow
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detection_method: Mapped[str] = mapped_column(
        String(64), nullable=False, default="heuristic"
    )  # heuristic, pattern, llm_judge
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_failure_class_trace_id", "trace_id"),
        Index("ix_failure_class_type", "failure_type"),
    )


class RemediationAction(Base):
    """Automated remediation action taken in response to a failure."""

    __tablename__ = "remediation_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    failure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("failure_classifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # retry_node, switch_tool, reduce_context, notify_webhook
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending, executed, success, failed
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_remediation_trace_id", "trace_id"),
        Index("ix_remediation_status", "status"),
    )
