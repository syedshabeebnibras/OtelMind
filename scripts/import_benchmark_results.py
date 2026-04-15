"""Import benchmark JSON files into the group_runs + group_messages tables.

Populates the dashboard without spending any Anthropic credits — we already
paid for the runs once during `scripts/run_benchmarks.py`, this just makes
the results visible at /multiagent.

Idempotent: skips benchmark files whose (tenant, scenario_id, protocol)
tuple is already present. Safe to re-run after topping up credits and
filling in the failed runs.

Usage:
    .venv/bin/python scripts/import_benchmark_results.py              # uses first active tenant
    .venv/bin/python scripts/import_benchmark_results.py --tenant <slug>
    .venv/bin/python scripts/import_benchmark_results.py --skip-failed  # drop status=failed JSONs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid as _uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from otelmind.db import get_session  # noqa: E402
from otelmind.storage.models import GroupMessage, GroupRun, Tenant  # noqa: E402

RESULTS_DIR = REPO_ROOT / "config" / "eval_datasets" / "benchmark_results"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    # Strip trailing 'Z' and add explicit UTC
    s = value.rstrip("Z")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _resolve_tenant(session, slug: str | None) -> Tenant:
    if slug:
        t = await session.scalar(
            select(Tenant).where(Tenant.slug == slug, Tenant.is_active.is_(True))
        )
        if t is None:
            raise SystemExit(f"No active tenant with slug {slug!r}")
        return t
    # Default: oldest active tenant (the one bootstrap_tenant created first).
    t = await session.scalar(
        select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.created_at).limit(1)
    )
    if t is None:
        raise SystemExit("No active tenants in the DB — run `otelmind-bootstrap` first.")
    return t


async def _existing_scenarios_for_tenant(session, tenant_id) -> set[tuple[str, str]]:
    """Return (problem, protocol) tuples already imported for this tenant."""
    rows = (
        await session.execute(
            select(GroupRun.problem, GroupRun.protocol).where(GroupRun.tenant_id == tenant_id)
        )
    ).all()
    return {(p, proto) for p, proto in rows}


async def _import_one(session, tenant_id, payload: dict, existing: set[tuple[str, str]]) -> str:
    """Insert one benchmark JSON. Returns status string for the logger."""
    scenario_id = payload.get("scenario_id", "unknown")
    protocol = payload.get("protocol", "unknown")

    if "error" in payload:
        # Top-level error (usually: the script never produced a result object)
        # Persist as a failed row so the dashboard still shows it.
        run = GroupRun(
            id=_uuid.uuid4(),
            tenant_id=tenant_id,
            problem=f"[benchmark:{scenario_id}]",
            protocol=protocol,
            status="failed",
            roles=[],
            result={"error": payload["error"]},
            metrics=None,
            rounds_completed=0,
            total_tokens=0,
            total_cost_usd=0.0,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        if (run.problem, protocol) in existing:
            return "skip (exists)"
        session.add(run)
        await session.flush()
        return "imported (error payload)"

    result = payload.get("result") or {}
    metrics = payload.get("metrics") or {}
    messages = result.get("messages") or []
    problem = result.get("problem") or f"[benchmark:{scenario_id}]"
    problem_key = problem  # match the column used in the dedup query

    if (problem_key, protocol) in existing:
        return "skip (exists)"

    run_id = _uuid.uuid4()
    run = GroupRun(
        id=run_id,
        tenant_id=tenant_id,
        problem=problem,
        protocol=protocol,
        status=result.get("status", "completed"),
        roles=result.get("roles") or [],
        result=result,
        metrics=metrics,
        rounds_completed=int(result.get("rounds_completed", 0) or 0),
        total_tokens=int(result.get("total_tokens", 0) or 0),
        total_cost_usd=float(metrics.get("total_cost_usd", 0.0) or 0.0),
        created_at=_parse_iso(result.get("started_at")) or datetime.now(UTC),
        completed_at=_parse_iso(result.get("completed_at")) or datetime.now(UTC),
    )
    session.add(run)
    # Flush the GroupRun row first so the FK on GroupMessage can resolve.
    await session.flush()

    for m in messages:
        session.add(
            GroupMessage(
                group_run_id=run_id,
                sender_id=str(m.get("sender_id", "unknown")),
                sender_role=str(m.get("sender_role", "unknown")),
                recipient_id=m.get("recipient_id"),
                content=str(m.get("content", "")),
                round_number=int(m.get("round_number", 0) or 0),
                token_usage=m.get("token_usage"),
            )
        )
    await session.flush()

    existing.add((problem_key, protocol))
    return f"imported ({len(messages)} messages, {run.total_tokens:,} tokens)"


async def main(tenant_slug: str | None, skip_failed: bool) -> int:
    if not RESULTS_DIR.exists():
        print(
            f"ERROR: {RESULTS_DIR} does not exist — run scripts/run_benchmarks.py first",
            file=sys.stderr,
        )
        return 1

    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        print(f"ERROR: no benchmark JSON files in {RESULTS_DIR}", file=sys.stderr)
        return 1

    print(f"Importing {len(files)} benchmark files into the DB...")

    imported = skipped = errors = 0
    async with get_session() as session:
        tenant = await _resolve_tenant(session, tenant_slug)
        print(f"  tenant: {tenant.slug} ({tenant.id})")
        existing = await _existing_scenarios_for_tenant(session, tenant.id)

        for path in files:
            try:
                with path.open() as fh:
                    payload = json.load(fh)
            except Exception as exc:
                print(f"  {path.name}: READ ERROR — {exc}")
                errors += 1
                continue

            if skip_failed and (
                (payload.get("result") or {}).get("status") == "failed" or "error" in payload
            ):
                print(f"  {path.name}: skip (failed, --skip-failed)")
                skipped += 1
                continue

            status = await _import_one(session, tenant.id, payload, existing)
            print(f"  {path.name}: {status}")
            if status.startswith("skip"):
                skipped += 1
            else:
                imported += 1

    print(f"\nDone. imported={imported} skipped={skipped} errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="Tenant slug (default: oldest active)")
    parser.add_argument(
        "--skip-failed", action="store_true", help="Skip benchmark rows with status=failed"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.tenant, args.skip_failed)))
