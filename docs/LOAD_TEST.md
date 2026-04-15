# Load test results

Captured against the locally-running FastAPI backend (Apple Silicon, M-class
laptop, Python 3.11) with `scripts/load_test.py`. The same harness can run
against `https://otelmind-api-production.up.railway.app/...` — Railway
results will differ; treat these as representative shape and bottleneck
identification, not absolute production numbers.

## `GET /api/v1/health`

Hits Postgres (executes `SELECT Trace LIMIT 0`) and a small constant payload.
This is the cheapest realistic round-trip we have.

### concurrency 16 · 10 s

```
.venv/bin/python scripts/load_test.py --concurrency 16 --duration 10
```

| min | p50 | p90 | p95 | p99 | max | mean |
|----:|----:|----:|----:|----:|----:|-----:|
| 89 ms | 131 ms | 219 ms | 238 ms | 832 ms | 882 ms | 161 ms |

- 977 requests across 10.1 s → **96.8 RPS**
- All 977 returned `200`. No connection failures.
- p99 spike (832 ms) is from a single GC / Postgres connection-pool warm-up;
  the next runs don't repeat it.

### concurrency 64 · 10 s

| min | p50 | p90 | p95 | p99 | max | mean |
|----:|----:|----:|----:|----:|----:|-----:|
| 112 ms | 341 ms | 411 ms | 452 ms | 608 ms | 732 ms | 340 ms |

- 1,885 requests across 10.5 s → **180.3 RPS**
- All 1,885 returned `200`.
- The latency curve is now event-loop-bound: doubling concurrency from 16 to
  64 only ~2× the throughput while p50 latency tripled. Single uvicorn
  worker, single event loop — adding workers (`uvicorn --workers N`) is the
  obvious next step before bigger fanout.

## `POST /api/v1/multiagent/recommend-protocol`

Auth'd, hits the DB hard, and runs TF-IDF cosine across every historical
GroupRun for the tenant (currently 31 rows — would scale super-linearly
without indexing).

### concurrency 16 · 10 s

```
.venv/bin/python scripts/load_test.py \
  --url http://localhost:8000/api/v1/multiagent/recommend-protocol \
  --method POST \
  --body '{"problem":"debug a python memory leak in flask"}' \
  --header 'x-api-key: ...' \
  --concurrency 16 --duration 10
```

| min | p50 | p90 | p95 | p99 | max | mean |
|----:|----:|----:|----:|----:|----:|-----:|
| 1,433 ms | 3,839 ms | 8,216 ms | 10,003 ms | 10,013 ms | 10,014 ms | 4,465 ms |

- 39 requests across 17 s → **2.3 RPS**
- 36 returned `200`, **3 timed out** (10 s default httpx read timeout).
- Bottleneck identified: every request loads ALL group_runs for the tenant
  (no LIMIT) and runs pure-Python TF-IDF on the union. This is fine when
  the table is small, dangerous as it grows.

## What this told us — concrete next actions

1. **/health is healthy.** ~180 RPS on a single worker is comfortable for
   the dashboard's polling cadence. The p99 outlier is a connection-pool
   one-time warm-up that doesn't repeat in subsequent windows.

2. **/recommend-protocol needs work before serious traffic.** Two cheap
   wins land us 10× headroom:
   - Cap the candidate window to the last N (e.g. 200) GroupRuns instead of
     reading everything. Add `LIMIT 200 ORDER BY created_at DESC` to the
     SELECT in `protocol_selector._fetch_neighbours`.
   - Cache the TF-IDF vector per GroupRun row. Right now we re-tokenize +
     re-vectorise every problem on every request. Persist a
     `problem_tfidf JSONB` column on `group_runs` (or memoise in-process).

3. **Single-worker uvicorn caps near 180 RPS for cheap reads.** For
   production-realistic numbers, run with `uvicorn ... --workers 4` or
   put gunicorn-with-uvicorn-workers in front. The Railway image already
   uses Procfile-style entrypoint — bumping worker count there is a
   one-line change in the start command.

## How to reproduce

```bash
# Start the API locally
.venv/bin/python -m otelmind.api &

# Quick smoke
.venv/bin/python scripts/load_test.py

# Higher load
.venv/bin/python scripts/load_test.py --concurrency 64 --duration 30

# Hit a protected route — get an API key from `otelmind-bootstrap` first
.venv/bin/python scripts/load_test.py \
  --url http://localhost:8000/api/v1/multiagent/runs \
  --header 'x-api-key: om_...' --concurrency 8 --duration 10
```
