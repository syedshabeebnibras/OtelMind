"""Live evaluation workers — the three loops that turn the static eval
module into a continuous-quality system.

1. `eval_run_worker_loop()`
   Polls `eval_runs WHERE status='pending'` and executes
   `run_regression()` on the attached cases. Writes per-dimension
   scores, regression/improvement counts, and pass/fail back to the
   same row. Drives the "Run eval" button in the dashboard.

2. `trace_autoscorer_loop()`
   Samples a configurable fraction of newly-completed traces that
   haven't been scored yet and runs them through `LLMJudge.score()`.
   Writes one row per dimension into `trace_scores`. Drives the
   rolling-24h quality KPIs.

3. `daily_golden_regression_loop()`
   Once per day (at a configurable UTC hour), loads a golden dataset
   from disk, records it as a new "daily-golden-YYYY-MM-DD" EvalRun,
   scores every case via the judge, compares against yesterday's run,
   and fires an alert via `AlertRouter` if any dimension dropped more
   than `eval_regression_threshold`.

All three are started from the FastAPI `lifespan` alongside the
watchdog and partition-maintenance loops. Exceptions inside each tick
are swallowed and logged so a transient failure can't take the API
down.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid as _uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import desc, select

from otelmind.config import settings
from otelmind.db import get_session
from otelmind.eval.judge import DIMENSIONS, JudgeResult, LLMJudge
from otelmind.eval.regression import EvalCase, run_regression
from otelmind.storage.models import (
    EvalRun,
    FailureClassification,
    Span,
    Tenant,
    Trace,
    TraceScore,
)

# ─── 1. Eval run worker ───────────────────────────────────────────────


async def _claim_pending_eval_run() -> EvalRun | None:
    """Atomically pick the oldest pending eval run and mark it running.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple worker instances
    never grab the same row. The status flip to 'running' commits
    inside this function so a subsequent failure leaves it recoverable
    via `update eval_runs set status='pending' where status='running'`
    manual retry.
    """
    async with get_session() as session:
        stmt = (
            select(EvalRun)
            .where(EvalRun.status == "pending")
            .order_by(EvalRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        run = (await session.execute(stmt)).scalar_one_or_none()
        if run is None:
            return None
        run.status = "running"
        return run


def _extract_cases_from_details(details: dict[str, Any] | None) -> tuple[
    list[EvalCase], list[EvalCase]
]:
    """Pull baseline/candidate case lists out of the EvalRun.details JSON blob.

    The POST /evals endpoint accepts cases under these keys. We tolerate
    both flat dicts (no cases — worker marks the run as empty) and the
    full shape. Missing keys mean "no cases to compare."
    """
    if not details:
        return [], []

    def _parse(raw: list[dict[str, Any]] | None) -> list[EvalCase]:
        out: list[EvalCase] = []
        for item in raw or []:
            out.append(
                EvalCase(
                    id=str(item.get("id", "")),
                    question=str(item.get("question", "")),
                    expected=str(item.get("expected", "")),
                    actual=str(item.get("actual", "")),
                    context=str(item.get("context", "")),
                    tags=list(item.get("tags", [])),
                )
            )
        return out

    return _parse(details.get("baseline_cases")), _parse(details.get("candidate_cases"))


async def _execute_eval_run(run_id: _uuid.UUID) -> None:
    """Run the regression comparator against the cases in an EvalRun and
    write results back. Exceptions are logged and the row is marked failed."""
    async with get_session() as session:
        run = await session.scalar(select(EvalRun).where(EvalRun.id == run_id))
        if run is None:
            return

        baseline, candidate = _extract_cases_from_details(run.details)
        if not baseline or not candidate:
            # No cases attached — mark completed with zero scores so the
            # dashboard shows "0 cases" instead of a stuck "pending" row.
            run.status = "completed"
            run.passed = True
            run.scores = {}
            run.case_count = 0
            run.completed_at = datetime.now(UTC)
            logger.info(
                "eval: run {} has no cases, marking empty-complete", run.id
            )
            return

    try:
        report = await run_regression(
            baseline,
            candidate,
            baseline_name=run.baseline or "baseline",
            candidate_name=run.candidate or "candidate",
            regression_threshold=settings.eval_regression_threshold,
            api_key=settings.llm.api_key or None,
        )
    except Exception as exc:
        logger.exception("eval: regression failed for run {}: {}", run_id, exc)
        async with get_session() as session:
            row = await session.scalar(select(EvalRun).where(EvalRun.id == run_id))
            if row is not None:
                row.status = "failed"
                row.details = {**(row.details or {}), "error": str(exc)}
                row.completed_at = datetime.now(UTC)
        return

    # Aggregate per-dimension mean score — display on the dashboard.
    dim_summary: dict[str, float] = {}
    if report.per_case:
        for dim in DIMENSIONS:
            values = [
                c["dimensions"][dim]["candidate"]
                for c in report.per_case
                if dim in c.get("dimensions", {})
            ]
            if values:
                dim_summary[dim] = round(sum(values) / len(values), 4)

    async with get_session() as session:
        row = await session.scalar(select(EvalRun).where(EvalRun.id == run_id))
        if row is None:
            return
        row.status = "completed"
        row.passed = report.passed
        row.scores = dim_summary
        row.regression_count = len(report.regressions)
        row.improvement_count = len(report.improvements)
        row.case_count = report.summary.get("total_cases", 0)
        # Trim the per-case detail to keep the row small — full report
        # is too big for the average UI fetch.
        row.details = {
            "summary": report.summary,
            "regressions": report.regressions[:50],
            "improvements": report.improvements[:50],
        }
        row.completed_at = datetime.now(UTC)

    logger.info(
        "eval: run {} completed passed={} regressions={} improvements={}",
        run_id,
        report.passed,
        len(report.regressions),
        len(report.improvements),
    )


async def eval_run_worker_loop() -> None:
    """Background loop — drain the pending eval run queue."""
    interval = settings.eval_worker_interval_seconds
    logger.info("eval: worker loop started, poll interval {}s", interval)
    while True:
        try:
            run = await _claim_pending_eval_run()
            if run is not None:
                await _execute_eval_run(run.id)
                # Loop back immediately to drain bursts.
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("eval worker tick failed: {}", exc)
        await asyncio.sleep(interval)


# ─── 2. Trace auto-scoring ────────────────────────────────────────────


async def _pick_unscored_traces(
    tenant: Tenant, limit: int
) -> list[Trace]:
    """Return recently-completed traces that have no rows in trace_scores yet.

    Filters to the last hour to cap the work even at high volume — if
    the loop misses a trace beyond that window we don't retroactively
    score it. Good enough for continuous monitoring; not an archival
    audit trail.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    async with get_session() as session:
        scored_subq = select(TraceScore.trace_id).where(
            TraceScore.tenant_id == tenant.id
        )
        stmt = (
            select(Trace)
            .where(
                Trace.tenant_id == tenant.id,
                Trace.end_time.isnot(None),
                Trace.created_at >= cutoff,
                ~Trace.trace_id.in_(scored_subq),
            )
            .order_by(desc(Trace.created_at))
            .limit(limit * 4)  # overfetch — we'll sample downstream
        )
        rows = list((await session.execute(stmt)).scalars().all())
    return rows


async def _load_question_answer(trace: Trace) -> tuple[str, str, str]:
    """Reconstruct (question, answer, context) from a trace's spans.

    Uses naive heuristics:
    * The earliest span's `inputs` is treated as the question.
    * The latest span's `outputs` is treated as the answer.
    * Any mid-trace retrieval spans contribute to the context.
    This will not be perfect for every agent shape, but matches the
    common "query → tool calls → final answer" pattern.
    """
    async with get_session() as session:
        stmt = (
            select(Span)
            .where(
                Span.tenant_id == trace.tenant_id,
                Span.trace_id == trace.trace_id,
            )
            .order_by(Span.start_time)
        )
        spans = list((await session.execute(stmt)).scalars().all())

    if not spans:
        return "", "", ""

    def _stringify(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload[:2000]
        try:
            return json.dumps(payload, default=str)[:2000]
        except Exception:
            return str(payload)[:2000]

    question = _stringify(spans[0].inputs) or spans[0].name
    answer = _stringify(spans[-1].outputs) or ""
    context_chunks: list[str] = []
    for s in spans[1:-1]:
        if s.outputs is not None and "retriev" in (s.name or "").lower():
            context_chunks.append(_stringify(s.outputs))
    context = "\n".join(context_chunks)[:2000]
    return question, answer, context


async def _score_trace(trace: Trace, judge: LLMJudge) -> JudgeResult | None:
    question, answer, context = await _load_question_answer(trace)
    if not answer:
        return None
    try:
        return await judge.score(
            question=question,
            answer=answer,
            context=context,
            dimensions=list(DIMENSIONS),
        )
    except Exception as exc:
        logger.warning("eval: scoring trace {} failed: {}", trace.trace_id, exc)
        return None


async def _persist_trace_scores(
    tenant_id: _uuid.UUID, trace_id: str, result: JudgeResult
) -> None:
    async with get_session() as session:
        for dim, score in result.scores.items():
            session.add(
                TraceScore(
                    id=_uuid.uuid4(),
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                    dimension=dim,
                    score=score.score,
                    raw_score=score.raw_score,
                    method=score.method,
                    reason=score.reason,
                )
            )


async def _autoscore_tenant(tenant: Tenant, judge: LLMJudge) -> int:
    batch = await _pick_unscored_traces(
        tenant, limit=settings.eval_autoscorer_batch_size
    )
    if not batch:
        return 0

    # Apply sample rate. 0.1 means roughly every 10th trace gets scored.
    rate = max(0.0, min(1.0, settings.eval_autoscorer_sample_rate))
    sampled = [t for t in batch if random.random() < rate][
        : settings.eval_autoscorer_batch_size
    ]
    if not sampled:
        return 0

    scored = 0
    for trace in sampled:
        result = await _score_trace(trace, judge)
        if result is None:
            continue
        await _persist_trace_scores(tenant.id, trace.trace_id, result)
        scored += 1
    return scored


async def trace_autoscorer_loop() -> None:
    """Background loop — sample + score new traces across all tenants."""
    interval = settings.eval_autoscorer_interval_seconds
    if settings.eval_autoscorer_sample_rate <= 0:
        logger.info("eval: auto-scorer disabled (sample_rate=0)")
        return
    logger.info(
        "eval: auto-scorer loop started, interval {}s, sample_rate {}",
        interval,
        settings.eval_autoscorer_sample_rate,
    )
    judge = LLMJudge(api_key=settings.llm.api_key or None, model=settings.llm.model or "gpt-4o")
    while True:
        try:
            async with get_session() as session:
                tenants = list(
                    (
                        await session.execute(
                            select(Tenant).where(Tenant.is_active.is_(True))
                        )
                    )
                    .scalars()
                    .all()
                )
            total = 0
            for tenant in tenants:
                total += await _autoscore_tenant(tenant, judge)
            if total > 0:
                logger.info("eval: auto-scored {} traces this tick", total)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("eval auto-scorer tick failed: {}", exc)
        await asyncio.sleep(interval)


# ─── 3. Daily golden-dataset regression ───────────────────────────────


def _load_golden_dataset(path: str) -> list[dict[str, Any]] | None:
    """Load a YAML/JSON golden dataset from disk. Returns None if missing."""
    p = Path(path)
    if not p.exists():
        return None
    raw = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            logger.warning("eval: yaml not installed, cannot load {}", path)
            return None
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    if isinstance(data, dict) and "cases" in data:
        return list(data["cases"])
    if isinstance(data, list):
        return data
    return None


async def _get_previous_golden_run(
    tenant_id: _uuid.UUID, exclude_run_id: _uuid.UUID | None = None
) -> EvalRun | None:
    """Return the most recent completed golden run, optionally excluding
    the one we just wrote. Without the exclusion, the caller would get
    its own freshly-completed row back and compare it to itself."""
    async with get_session() as session:
        stmt = (
            select(EvalRun)
            .where(
                EvalRun.tenant_id == tenant_id,
                EvalRun.name.like("daily-golden-%"),
                EvalRun.status == "completed",
            )
            .order_by(desc(EvalRun.created_at))
            .limit(2)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        for row in rows:
            if exclude_run_id is None or row.id != exclude_run_id:
                return row
        return None


async def _fire_regression_alert(
    tenant: Tenant, run: EvalRun, previous: EvalRun, regressed_dims: list[str]
) -> None:
    """Write a FailureClassification row and let the watchdog alerting
    pipeline route it. Avoids importing alert_router directly — the
    existing watchdog loop already scans new failure_classifications."""
    async with get_session() as session:
        failure = FailureClassification(
            id=_uuid.uuid4(),
            tenant_id=tenant.id,
            trace_id=f"eval-{run.id}",
            failure_type="eval_regression",
            confidence=0.95,
            evidence={
                "run_id": str(run.id),
                "previous_run_id": str(previous.id),
                "regressed_dimensions": regressed_dims,
                "current_scores": run.scores,
                "previous_scores": previous.scores,
            },
            detection_method="daily_golden",
        )
        session.add(failure)
    logger.warning(
        "eval: daily-golden regression on tenant {} — dims {}",
        tenant.slug,
        regressed_dims,
    )


async def _run_daily_golden_for_tenant(tenant: Tenant) -> None:
    cases_raw = _load_golden_dataset(settings.eval_golden_dataset_path)
    if not cases_raw:
        return

    # Convert to the shape `run_regression` wants. Golden datasets
    # define `expected` — we treat "the golden answer" as both the
    # baseline and the candidate, then score them separately after
    # we *also* generate a candidate by re-running the current agent.
    # Here we don't actually re-execute the agent (that's a user-level
    # concern); we just score the candidate answers the dataset file
    # provides under `candidate_actual`, so the YAML can be refreshed
    # daily by an external CI job that knows how to run the agent.
    baseline_cases = [
        EvalCase(
            id=str(c.get("id", "")),
            question=str(c.get("question", "")),
            expected=str(c.get("expected", "")),
            actual=str(c.get("expected", "")),  # baseline = golden truth
            context=str(c.get("context", "")),
        )
        for c in cases_raw
    ]
    candidate_cases = [
        EvalCase(
            id=str(c.get("id", "")),
            question=str(c.get("question", "")),
            expected=str(c.get("expected", "")),
            actual=str(c.get("candidate_actual", c.get("expected", ""))),
            context=str(c.get("context", "")),
        )
        for c in cases_raw
    ]

    today = date.today().isoformat()
    run_name = f"daily-golden-{today}"

    # Upsert — if today's run already exists (e.g. the loop wakes up
    # twice in the same day), reuse it.
    async with get_session() as session:
        existing = await session.scalar(
            select(EvalRun).where(
                EvalRun.tenant_id == tenant.id, EvalRun.name == run_name
            )
        )
        if existing is not None:
            return
        run = EvalRun(
            id=_uuid.uuid4(),
            tenant_id=tenant.id,
            name=run_name,
            baseline="golden",
            candidate="current",
            dataset=settings.eval_golden_dataset_path,
            status="running",
            details={
                "baseline_cases": [
                    {
                        "id": c.id,
                        "question": c.question,
                        "expected": c.expected,
                        "actual": c.actual,
                        "context": c.context,
                    }
                    for c in baseline_cases
                ],
                "candidate_cases": [
                    {
                        "id": c.id,
                        "question": c.question,
                        "expected": c.expected,
                        "actual": c.actual,
                        "context": c.context,
                    }
                    for c in candidate_cases
                ],
            },
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    await _execute_eval_run(run_id)

    # Compare against previous golden run, fire alert on regression.
    async with get_session() as session:
        current = await session.scalar(select(EvalRun).where(EvalRun.id == run_id))
        if current is None or current.status != "completed":
            return

    previous = await _get_previous_golden_run(tenant.id, exclude_run_id=current.id)
    if previous is None or not previous.scores:
        return

    threshold = settings.eval_regression_threshold
    regressed: list[str] = []
    for dim, prev_score in (previous.scores or {}).items():
        curr_score = (current.scores or {}).get(dim)
        if curr_score is None:
            continue
        if prev_score - curr_score > threshold:
            regressed.append(dim)

    if regressed:
        await _fire_regression_alert(tenant, current, previous, regressed)


async def daily_golden_regression_loop() -> None:
    """Background loop — runs golden-dataset regression once per day."""
    last_run_date: date | None = None
    logger.info(
        "eval: daily golden loop started, target UTC hour {}, dataset {}",
        settings.eval_daily_run_utc_hour,
        settings.eval_golden_dataset_path,
    )
    while True:
        try:
            now = datetime.now(UTC)
            should_fire = (
                now.hour >= settings.eval_daily_run_utc_hour
                and last_run_date != now.date()
            )
            if should_fire:
                async with get_session() as session:
                    tenants = list(
                        (
                            await session.execute(
                                select(Tenant).where(Tenant.is_active.is_(True))
                            )
                        )
                        .scalars()
                        .all()
                    )
                for tenant in tenants:
                    await _run_daily_golden_for_tenant(tenant)
                last_run_date = now.date()
                logger.info("eval: daily golden tick complete for {} tenants", len(tenants))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("eval daily golden tick failed: {}", exc)
        # Check every 10 minutes — cheap, and catches the target hour
        # within reasonable latency without a precise cron scheduler.
        await asyncio.sleep(600)
