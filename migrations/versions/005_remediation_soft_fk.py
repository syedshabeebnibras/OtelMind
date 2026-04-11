"""Restore referential lookups for remediation_actions after partitioning.

Revision ID: 005
Revises: 004
Create Date: 2026-04-11

Context
-------
Migration 003 converted `failure_classifications` into a range-partitioned
table with a compound primary key `(id, created_at)`. Postgres does not
allow a foreign key that references only part of a compound primary key,
so the pre-existing
    remediation_actions.failure_id → failure_classifications.id
constraint had to be dropped (CASCADE swept it when `failure_classifications_legacy`
was deleted).

We do not restore that FK. Adding `failure_created_at` to
`remediation_actions` and making it partitioned would be an option, but:

* Remediation rows are low-volume — they age at the same rate as failures,
  which are retention-managed by the partition dropper.
* All service-layer writes already have both IDs, so integrity is
  enforced above the database.
* `trace_id` references on spans / token_counts / failures use the same
  soft-FK pattern for the same reason, so this matches existing practice.

This migration just adds an index on `failure_id` so joins from the
dashboard "failure → remediation history" view stay cheap, and drops the
dangling constraint name in case it lingered from a partial upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Defensive drop: if a partial 003 left the old FK in place for any
    # reason, remove it now. IF EXISTS makes this a no-op on clean DBs.
    op.execute(
        "ALTER TABLE remediation_actions "
        "DROP CONSTRAINT IF EXISTS remediation_actions_failure_id_fkey"
    )
    op.execute(
        "ALTER TABLE remediation_actions "
        "DROP CONSTRAINT IF EXISTS fk_remediation_failure_id"
    )

    op.create_index(
        "ix_remediation_failure_id",
        "remediation_actions",
        ["failure_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_remediation_failure_id", table_name="remediation_actions")
