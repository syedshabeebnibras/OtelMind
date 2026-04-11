"""Monthly range partitioning for hot tables + RBAC (roles, users, permissions).

Revision ID: 003
Revises: 002
Create Date: 2026-04-11

Two concerns bundled because both touch the tenants/audit/telemetry schema
in ways that depend on each other:

1. Partitioning — converts `traces`, `spans`, `failure_classifications`,
   `token_counts`, and `audit_logs` into PostgreSQL range-partitioned tables
   keyed on `created_at`. Includes a helper function
   `otelmind_ensure_month_partition(parent, month_start)` so the application
   can lazily materialize the current/next month and a cron can drop old
   ones per tenant retention.

2. RBAC — introduces `users`, `roles`, `role_permissions`, and
   `user_tenant_roles` so enterprise customers can attach human users to a
   tenant with scoped permissions (traces:read, alerts:write, etc.) in
   addition to the existing API-key scope model.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Seeded roles — match the scopes used by the API-key auth layer.
DEFAULT_ROLES: list[tuple[str, str, list[str]]] = [
    (
        "owner",
        "Full control over the tenant, including billing and member management.",
        ["*"],
    ),
    (
        "admin",
        "Manage alerts, API keys, and telemetry data.",
        [
            "traces:read",
            "traces:write",
            "failures:read",
            "alerts:read",
            "alerts:write",
            "cost:read",
            "evals:read",
            "evals:write",
            "apikeys:read",
            "apikeys:write",
            "audit:read",
        ],
    ),
    (
        "engineer",
        "Read telemetry and manage alerts. No billing or key management.",
        [
            "traces:read",
            "failures:read",
            "alerts:read",
            "alerts:write",
            "cost:read",
            "evals:read",
            "evals:write",
        ],
    ),
    (
        "viewer",
        "Read-only access to traces, failures, cost, and evals.",
        [
            "traces:read",
            "failures:read",
            "cost:read",
            "evals:read",
        ],
    ),
    (
        "billing",
        "Cost and usage reports only — intended for finance stakeholders.",
        ["cost:read"],
    ),
]


# Tables converted to monthly range partitioning. Order matters: drop FKs
# that reference these before recreating.
PARTITIONED_TABLES = [
    "traces",
    "spans",
    "failure_classifications",
    "token_counts",
    "audit_logs",
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. RBAC tables
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Attach a user to a tenant with a role. A user can belong to many
    # tenants (multi-workspace) with different roles in each.
    op.create_table(
        "user_tenant_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
    )
    op.create_index("ix_utr_tenant_id", "user_tenant_roles", ["tenant_id"])
    op.create_index("ix_utr_user_id", "user_tenant_roles", ["user_id"])

    # Seed the default role set. Tenant admins cannot edit these (is_system).
    for name, desc, perms in DEFAULT_ROLES:
        role_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"otelmind.role.{name}"))
        perm_literal = "ARRAY[" + ",".join(f"'{p}'" for p in perms) + "]::varchar[]"
        op.execute(
            f"""
            INSERT INTO roles (id, name, description, permissions, is_system)
            VALUES (
                '{role_id}'::uuid,
                '{name}',
                '{desc.replace("'", "''")}',
                {perm_literal},
                true
            )
            """
        )

    # ------------------------------------------------------------------
    # 2. Monthly range partitioning helper
    # ------------------------------------------------------------------
    # One SQL function that creates `<parent>_YYYY_MM` partitions on demand.
    # Safe to call repeatedly thanks to IF NOT EXISTS. The API layer calls
    # this at startup for the current + next month, and a Celery beat or
    # cron job drops expired partitions per tenant retention policy.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION otelmind_ensure_month_partition(
            parent text,
            month_start date
        ) RETURNS void AS $$
        DECLARE
            part_name text;
            next_month date;
        BEGIN
            part_name := parent || '_' || to_char(month_start, 'YYYY_MM');
            next_month := (month_start + interval '1 month')::date;
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                part_name, parent, month_start, next_month
            );
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION otelmind_drop_expired_partitions(
            parent text,
            cutoff date
        ) RETURNS int AS $$
        DECLARE
            rec record;
            dropped int := 0;
        BEGIN
            FOR rec IN
                SELECT child.relname AS part_name,
                       pg_get_expr(child.relpartbound, child.oid) AS bound
                FROM pg_inherits
                JOIN pg_class parent_cls ON pg_inherits.inhparent = parent_cls.oid
                JOIN pg_class child ON pg_inherits.inhrelid = child.oid
                WHERE parent_cls.relname = parent
            LOOP
                IF rec.bound ~ 'TO \\(''(\\d{4}-\\d{2}-\\d{2})''\\)' THEN
                    IF (regexp_match(rec.bound, 'TO \\(''(\\d{4}-\\d{2}-\\d{2})''\\)'))[1]::date <= cutoff THEN
                        EXECUTE format('DROP TABLE IF EXISTS %I', rec.part_name);
                        dropped := dropped + 1;
                    END IF;
                END IF;
            END LOOP;
            RETURN dropped;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # ------------------------------------------------------------------
    # 3. Convert hot tables to PARTITION BY RANGE (created_at)
    # ------------------------------------------------------------------
    # PostgreSQL can't alter an existing table to be partitioned, so we:
    #   a. rename the original → <name>_legacy
    #   b. recreate the table (LIKE … INCLUDING ALL) as partitioned
    #   c. copy rows into the first monthly partition
    #   d. drop the legacy copy
    #
    # Primary keys on partitioned tables must include the partition key, so
    # we use a compound (id, created_at) PK. Unique constraints on other
    # columns become non-unique indexes (Postgres limitation).
    #
    # Explicitly drop dependent foreign keys before the rename so that the
    # DROP TABLE CASCADE at the bottom of the block can't silently remove
    # anything we haven't accounted for. Currently the only cross-table FK
    # that survives 002 is remediation_actions.failure_id, but we list
    # them by name + `IF EXISTS` so this is idempotent on partial re-runs.
    op.execute(
        "ALTER TABLE remediation_actions "
        "DROP CONSTRAINT IF EXISTS remediation_actions_failure_id_fkey"
    )
    op.execute(
        "ALTER TABLE remediation_actions "
        "DROP CONSTRAINT IF EXISTS fk_remediation_failure_id"
    )
    # spans / token_counts reference traces.trace_id via FKs declared in
    # migration 001 under auto-generated names. Drop them defensively so
    # the rename can't fail on a lock. Safe no-op if already gone.
    op.execute(
        "ALTER TABLE spans DROP CONSTRAINT IF EXISTS spans_trace_id_fkey"
    )
    op.execute(
        "ALTER TABLE token_counts DROP CONSTRAINT IF EXISTS token_counts_trace_id_fkey"
    )
    op.execute(
        "ALTER TABLE failure_classifications "
        "DROP CONSTRAINT IF EXISTS failure_classifications_trace_id_fkey"
    )
    # tool_errors.span_id references spans.span_id (via the unique index)
    # — drop it so we can strip the unique constraint on spans_legacy.
    # tool_errors is not partitioned; we keep the column as a soft ref.
    op.execute(
        "ALTER TABLE tool_errors DROP CONSTRAINT IF EXISTS tool_errors_span_id_fkey"
    )

    # Rename idempotently — on a partial re-run the legacy copy may
    # already exist, in which case we leave it alone and assume the
    # backfill block will finish the job.
    for tbl in PARTITIONED_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = '{tbl}')
                   AND NOT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = '{tbl}_legacy')
                THEN
                    EXECUTE 'ALTER TABLE {tbl} RENAME TO {tbl}_legacy';
                END IF;
            END$$;
            """
        )

    # Free up the index namespace. Postgres keeps indexes attached to
    # renamed tables under their original names, and index names are
    # unique database-wide — so `ix_traces_tenant_id` on traces_legacy
    # would block `CREATE INDEX ix_traces_tenant_id ON traces` below.
    # We drop every named index that the new partitioned tables want to
    # own, plus the implicit unique constraints from migration 001. The
    # legacy table itself still has its primary key and will be dropped
    # later in the backfill step, so full-table scans during backfill
    # are fine.
    legacy_indexes_to_drop = [
        # from migration 001
        "ix_traces_service_name",
        "ix_traces_start_time",
        "ix_traces_trace_id",
        "ix_spans_name",
        "ix_spans_span_id",
        "ix_spans_start_time",
        "ix_spans_trace_id",
        "ix_token_counts_trace_id",
        "ix_failure_class_trace_id",
        "ix_failure_class_type",
        # from migration 002 (multi-tenancy columns)
        "ix_traces_tenant_id",
        "ix_traces_tenant_service",
        "ix_traces_tenant_start_time",
        "ix_spans_tenant_trace_id",
        "ix_token_counts_tenant_id",
        "ix_token_counts_model",
        "ix_failure_class_tenant_type",
        "ix_failure_class_created_at",
        "ix_audit_logs_tenant_id",
        "ix_audit_logs_created_at",
    ]
    for idx in legacy_indexes_to_drop:
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    # Drop the unique constraint on traces.trace_id from migration 001 —
    # the partitioned replacement uses a non-unique index because a
    # partitioned table can't have a unique constraint that doesn't
    # include the partition key.
    op.execute(
        "ALTER TABLE traces_legacy "
        "DROP CONSTRAINT IF EXISTS traces_trace_id_key"
    )
    op.execute(
        "ALTER TABLE spans_legacy "
        "DROP CONSTRAINT IF EXISTS spans_span_id_key"
    )

    # traces
    op.execute(
        """
        CREATE TABLE traces (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            trace_id varchar(64) NOT NULL,
            service_name varchar(255) NOT NULL,
            status varchar(32) NOT NULL DEFAULT 'ok',
            start_time timestamptz NOT NULL,
            end_time timestamptz,
            duration_ms double precision,
            metadata jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_traces_tenant_id ON traces (tenant_id)")
    op.execute("CREATE INDEX ix_traces_trace_id ON traces (trace_id)")
    op.execute("CREATE INDEX ix_traces_tenant_service ON traces (tenant_id, service_name)")
    op.execute("CREATE INDEX ix_traces_tenant_start_time ON traces (tenant_id, start_time)")
    op.execute("CREATE UNIQUE INDEX uq_traces_trace_id_created ON traces (trace_id, created_at)")

    # spans — FK to traces.trace_id is impossible across partitions; we
    # denormalize tenant_id and rely on application-layer integrity.
    op.execute(
        """
        CREATE TABLE spans (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            span_id varchar(64) NOT NULL,
            trace_id varchar(64) NOT NULL,
            parent_span_id varchar(64),
            name varchar(255) NOT NULL,
            kind varchar(32) NOT NULL DEFAULT 'INTERNAL',
            status_code varchar(32) NOT NULL DEFAULT 'OK',
            start_time timestamptz NOT NULL,
            end_time timestamptz,
            duration_ms double precision,
            attributes jsonb,
            events jsonb,
            inputs jsonb,
            outputs jsonb,
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_spans_tenant_id ON spans (tenant_id)")
    op.execute("CREATE INDEX ix_spans_span_id ON spans (span_id)")
    op.execute("CREATE INDEX ix_spans_tenant_trace_id ON spans (tenant_id, trace_id)")
    op.execute("CREATE INDEX ix_spans_name ON spans (name)")
    op.execute("CREATE INDEX ix_spans_start_time ON spans (start_time)")

    # token_counts
    op.execute(
        """
        CREATE TABLE token_counts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            trace_id varchar(64) NOT NULL,
            span_id varchar(64),
            model_name varchar(255) NOT NULL,
            model_provider varchar(64) NOT NULL DEFAULT 'unknown',
            prompt_tokens integer NOT NULL DEFAULT 0,
            completion_tokens integer NOT NULL DEFAULT 0,
            total_tokens integer NOT NULL DEFAULT 0,
            cost_usd double precision NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_token_counts_tenant_id ON token_counts (tenant_id)")
    op.execute("CREATE INDEX ix_token_counts_trace_id ON token_counts (trace_id)")
    op.execute("CREATE INDEX ix_token_counts_model ON token_counts (tenant_id, model_name)")

    # failure_classifications
    op.execute(
        """
        CREATE TABLE failure_classifications (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            trace_id varchar(64) NOT NULL,
            failure_type varchar(64) NOT NULL,
            confidence double precision NOT NULL,
            evidence jsonb,
            detection_method varchar(64) NOT NULL DEFAULT 'heuristic',
            alerted boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_failure_class_tenant_type "
        "ON failure_classifications (tenant_id, failure_type)"
    )
    op.execute("CREATE INDEX ix_failure_class_trace_id ON failure_classifications (trace_id)")
    op.execute(
        "CREATE INDEX ix_failure_class_created_at "
        "ON failure_classifications (tenant_id, created_at)"
    )

    # audit_logs
    op.execute(
        """
        CREATE TABLE audit_logs (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            api_key_id uuid,
            user_id uuid,
            action varchar(128) NOT NULL,
            resource_id varchar(128),
            ip_address varchar(64),
            user_agent text,
            status_code integer,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_audit_logs_tenant_id ON audit_logs (tenant_id)")
    op.execute("CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at)")

    # ------------------------------------------------------------------
    # 4. Materialize the first partition and backfill data
    # ------------------------------------------------------------------
    # Create partitions for the current month and two months on either
    # side. The app will extend this rolling window on startup.
    op.execute(
        """
        DO $$
        DECLARE
            m date;
            t text;
            parents text[] := ARRAY['traces','spans','token_counts','failure_classifications','audit_logs'];
        BEGIN
            FOREACH t IN ARRAY parents LOOP
                FOR m IN
                    SELECT generate_series(
                        date_trunc('month', now() - interval '2 months')::date,
                        date_trunc('month', now() + interval '2 months')::date,
                        interval '1 month'
                    )::date
                LOOP
                    PERFORM otelmind_ensure_month_partition(t, m);
                END LOOP;
            END LOOP;
        END$$;
        """
    )

    # Backfill — copy legacy rows into the partitioned parent. Postgres
    # routes each row into the correct monthly child automatically.
    # Each INSERT is wrapped in a `DO $$` so the legacy table is only
    # read when it exists (idempotent on partial re-runs), and we
    # ON CONFLICT DO NOTHING so repeated runs don't collide on id PKs.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'traces_legacy') THEN
                INSERT INTO traces
                    (id, tenant_id, trace_id, service_name, status, start_time,
                     end_time, duration_ms, metadata, created_at)
                SELECT id, tenant_id, trace_id, service_name, status, start_time,
                       end_time, duration_ms, metadata,
                       COALESCE(created_at, start_time, now())
                FROM traces_legacy
                ON CONFLICT DO NOTHING;
            END IF;
        END$$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'spans_legacy') THEN
                INSERT INTO spans
                    (id, tenant_id, span_id, trace_id, parent_span_id, name, kind,
                     status_code, start_time, end_time, duration_ms, attributes,
                     events, inputs, outputs, error_message, created_at)
                SELECT id, tenant_id, span_id, trace_id, parent_span_id, name, kind,
                       status_code, start_time, end_time, duration_ms, attributes,
                       events, inputs, outputs, error_message,
                       COALESCE(created_at, start_time, now())
                FROM spans_legacy
                ON CONFLICT DO NOTHING;
            END IF;
        END$$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'token_counts_legacy') THEN
                INSERT INTO token_counts
                    (id, tenant_id, trace_id, span_id, model_name, model_provider,
                     prompt_tokens, completion_tokens, total_tokens, cost_usd, created_at)
                SELECT id, tenant_id, trace_id, span_id, model_name, model_provider,
                       prompt_tokens, completion_tokens, total_tokens, cost_usd,
                       COALESCE(created_at, now())
                FROM token_counts_legacy
                ON CONFLICT DO NOTHING;
            END IF;
        END$$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'failure_classifications_legacy') THEN
                INSERT INTO failure_classifications
                    (id, tenant_id, trace_id, failure_type, confidence, evidence,
                     detection_method, alerted, created_at)
                SELECT id, tenant_id, trace_id, failure_type, confidence, evidence,
                       detection_method, alerted,
                       COALESCE(created_at, now())
                FROM failure_classifications_legacy
                ON CONFLICT DO NOTHING;
            END IF;
        END$$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'audit_logs_legacy') THEN
                INSERT INTO audit_logs
                    (id, tenant_id, api_key_id, action, resource_id, ip_address,
                     user_agent, status_code, created_at)
                SELECT id, tenant_id, api_key_id, action, resource_id, ip_address,
                       user_agent, status_code, COALESCE(created_at, now())
                FROM audit_logs_legacy
                ON CONFLICT DO NOTHING;
            END IF;
        END$$;
        """
    )

    # Drop legacy tables now that rows are migrated. We use plain DROP
    # (no CASCADE) because all dependent FKs were explicitly removed
    # earlier in this migration. If a new FK has been added since the
    # rebuild started (e.g. by a racing migration), we'd rather fail
    # loudly here than silently lose it to a CASCADE.
    for tbl in PARTITIONED_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {tbl}_legacy")


def downgrade() -> None:
    # Collapse the partitioned tables back into plain tables. We keep the
    # data by renaming + SELECT INTO. RBAC tables just drop.
    for tbl in PARTITIONED_TABLES:
        op.execute(f"ALTER TABLE {tbl} RENAME TO {tbl}_partitioned")

    op.execute(
        """
        CREATE TABLE traces AS
        SELECT * FROM traces_partitioned
        """
    )
    op.execute(
        """
        CREATE TABLE spans AS
        SELECT * FROM spans_partitioned
        """
    )
    op.execute(
        """
        CREATE TABLE token_counts AS
        SELECT * FROM token_counts_partitioned
        """
    )
    op.execute(
        """
        CREATE TABLE failure_classifications AS
        SELECT * FROM failure_classifications_partitioned
        """
    )
    op.execute(
        """
        CREATE TABLE audit_logs AS
        SELECT * FROM audit_logs_partitioned
        """
    )

    for tbl in PARTITIONED_TABLES:
        op.execute(f"DROP TABLE {tbl}_partitioned CASCADE")

    op.execute("DROP FUNCTION IF EXISTS otelmind_drop_expired_partitions(text, date)")
    op.execute("DROP FUNCTION IF EXISTS otelmind_ensure_month_partition(text, date)")

    op.drop_index("ix_utr_user_id", table_name="user_tenant_roles")
    op.drop_index("ix_utr_tenant_id", table_name="user_tenant_roles")
    op.drop_table("user_tenant_roles")
    op.drop_table("roles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
