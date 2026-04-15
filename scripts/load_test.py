"""Tiny load-test harness for the OtelMind FastAPI backend.

Hammers a target URL with concurrent httpx GETs and reports throughput
and latency percentiles. Defaults to the unauth'd /api/v1/health endpoint
on the local server (no DB hits, no auth, no cost).

Usage:

    # quick local smoke
    .venv/bin/python scripts/load_test.py

    # custom target / concurrency / duration
    .venv/bin/python scripts/load_test.py \\
      --url https://otelmind-api-production.up.railway.app/api/v1/health \\
      --concurrency 32 --duration 30

    # POST with a body and an api key
    .venv/bin/python scripts/load_test.py \\
      --url http://localhost:8000/api/v1/multiagent/recommend-protocol \\
      --method POST --body '{"problem":"x"}' --header "x-api-key: om_..."

Prints a JSON summary on stdout AND a markdown table to stderr so the
output can be both consumed by tooling and dropped into a doc.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter

import httpx


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = p * (len(ordered) - 1)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (k - lo) * (ordered[hi] - ordered[lo])


async def _worker(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
    deadline: float,
    latencies_ms: list[float],
    statuses: Counter,
) -> None:
    while time.monotonic() < deadline:
        t0 = time.perf_counter()
        try:
            resp = await client.request(method, url, content=body, headers=headers)
            statuses[resp.status_code] += 1
        except Exception as exc:  # connection drops, timeouts, etc.
            statuses[f"err:{type(exc).__name__}"] += 1
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)


async def main(args: argparse.Namespace) -> int:
    headers = dict(h.split(":", 1) for h in args.header) if args.header else {}
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    body = args.body.encode() if args.body else None
    if body is not None:
        headers.setdefault("Content-Type", "application/json")

    deadline = time.monotonic() + args.duration
    latencies: list[float] = []
    statuses: Counter = Counter()
    timeout = httpx.Timeout(args.timeout, connect=args.timeout)
    started = time.monotonic()

    async with httpx.AsyncClient(timeout=timeout, http2=False) as client:
        # Warm up the connection so the first call's TLS/handshake doesn't
        # skew p50.
        try:
            await client.request(args.method, args.url, content=body, headers=headers)
        except Exception:
            pass

        await asyncio.gather(
            *[
                _worker(client, args.url, args.method, body, headers, deadline, latencies, statuses)
                for _ in range(args.concurrency)
            ]
        )

    elapsed = time.monotonic() - started
    n = len(latencies)
    rps = n / elapsed if elapsed > 0 else 0.0
    summary = {
        "url": args.url,
        "method": args.method,
        "concurrency": args.concurrency,
        "duration_s": round(elapsed, 3),
        "requests": n,
        "rps": round(rps, 2),
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0,
            "p50": round(_percentile(latencies, 0.50), 2),
            "p90": round(_percentile(latencies, 0.90), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
            "p99": round(_percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0,
            "mean": round(statistics.mean(latencies), 2) if latencies else 0,
        },
        "status_codes": dict(statuses),
    }
    print(json.dumps(summary, indent=2))

    # Markdown summary on stderr — easy to paste into docs/PRs
    table = (
        f"\n## {args.method} {args.url}\n\n"
        f"- duration {summary['duration_s']:.1f}s, concurrency {args.concurrency}, "
        f"{n:,} requests at **{rps:.1f} RPS**\n\n"
        "| min | p50 | p90 | p95 | p99 | max | mean |\n"
        "|----:|----:|----:|----:|----:|----:|-----:|\n"
        f"| {summary['latency_ms']['min']} ms "
        f"| {summary['latency_ms']['p50']} ms "
        f"| {summary['latency_ms']['p90']} ms "
        f"| {summary['latency_ms']['p95']} ms "
        f"| {summary['latency_ms']['p99']} ms "
        f"| {summary['latency_ms']['max']} ms "
        f"| {summary['latency_ms']['mean']} ms |\n\n"
        f"Status codes: {dict(statuses)}\n"
    )
    print(table, file=sys.stderr)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://localhost:8000/api/v1/health")
    p.add_argument("--method", default="GET")
    p.add_argument("--body", default=None, help="Request body for POST/PATCH")
    p.add_argument(
        "--header",
        action="append",
        default=[],
        help="Extra header `Name: value`. Can repeat.",
    )
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--duration", type=float, default=10.0, help="Wall-clock seconds")
    p.add_argument("--timeout", type=float, default=10.0)
    raise SystemExit(asyncio.run(main(p.parse_args())))
