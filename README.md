# OtelMind

Observability, failure detection, and self-healing for LLM agents.

## What It Does

- **Instrument** LangGraph (and other) agents via OpenTelemetry with a 3-line SDK.
- **Classify failures** — hallucinations, infinite loops, tool misuse, context overflow, semantic drift, cost spikes, prompt injection — using a mix of heuristics and an LLM judge.
- **Self-heal** via a remediation engine that retries, swaps tools, or escalates.
- **Continuously evaluate** traces and prompt changes with a G-Eval-style multi-dimensional judge, regression gates, and calibration against human labels.
- **Expose** traces, failures, scores, and alerts through a FastAPI backend, a Next.js dashboard, and an MCP server for AI assistants.

## Architecture

```
  ┌──────────────────┐   OTel   ┌──────────────┐   writes   ┌──────────────┐
  │ Instrumented App │ ───────► │  Collector   │ ─────────► │  PostgreSQL  │
  │ (LangGraph, JS)  │          │  (batch)     │            │  (Neon)      │
  └──────────────────┘          └──────────────┘            └──────┬───────┘
                                                                   │
                      ┌────────────────────┬──────────────────┬────┴─────────┐
                      ▼                    ▼                  ▼              ▼
                ┌───────────┐        ┌──────────┐       ┌──────────┐   ┌──────────┐
                │ Watchdog  │        │   Eval   │       │   API    │   │   MCP    │
                │ detectors │        │  worker  │       │ (FastAPI)│   │  server  │
                │ + judge   │        │ + judge  │       │          │   │          │
                └─────┬─────┘        └────┬─────┘       └────┬─────┘   └──────────┘
                      │                   │                  │
                      ▼                   ▼                  ▼
                ┌───────────┐        ┌──────────┐       ┌──────────┐
                │Remediation│        │ Regress. │       │Dashboard │
                │  engine   │        │ reports  │       │ (Next.js)│
                └───────────┘        └──────────┘       └──────────┘
```

- **Collector** — batches OTLP traces/spans/tokens/errors and flushes in one transaction per tick.
- **Watchdog** — heuristic detectors plus an optional LLM judge, classifies traces into failure types and emits evidence.
- **Eval worker** — runs queued `EvalRun` regressions, samples new traces for auto-scoring, and a daily golden-dataset regression.
- **Remediation engine** — retry, tool swap, or escalate (webhook / Slack / PagerDuty / email).
- **API** — multi-tenant FastAPI over SQLAlchemy 2.0 + asyncpg, with RBAC and rate limiting.
- **Dashboard** — Next.js App Router UI on Vercel.
- **MCP server** — exposes failure classification, hallucination check, benchmark runner, trace summary, multi-agent eval, and judge calibration as tools for Claude and other MCP clients.

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/syedshabeebnibras/OtelMind.git
cd OtelMind
cp .env.example .env
# edit .env: POSTGRES_*, LLM_API_KEY (OpenAI), ANTHROPIC_API_KEY (multi-agent)
```

### 2. Run the stack

```bash
docker compose up -d postgres redis
pip install -r requirements.txt
alembic upgrade head
python -m otelmind.api        # API on :8000
python -m otelmind.collector  # OTLP collector
python -m otelmind.watchdog   # watchdog loop
```

### 3. Instrument your app

```python
from otelmind.instrumentation import setup_tracer, instrument_langgraph

setup_tracer(service_name="my-agent")
instrument_langgraph(my_graph)
```

See `sdk-js/` for the JavaScript SDK.

## MCP Server

Install the MCP server for Claude Desktop or Claude Code:

```json
{
  "mcpServers": {
    "otelmind": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "env": {"OTELMIND_API_URL": "http://localhost:8000"}
    }
  }
}
```

Tools exposed:
- `classify_agent_failure` — run heuristics + LLM judge on a trace
- `check_hallucination` — grounded-generation check for a prompt/response pair
- `run_eval_benchmark` — execute a saved benchmark and return metrics
- `get_trace_summary` — cost, bottleneck, timeline for a trace
- `run_multiagent_eval` — spawn a multi-agent group and score their collaboration
- `calibrate_judge` — inter-rater agreement against human labels

See `mcp_server/README.md` for the full spec.

## Project Structure

```
otelmind/                      Python package
├── api/                       FastAPI routes, auth, rate limiting
├── alerting/                  Slack / PagerDuty / email channels
├── collector/                 OTLP receiver + batch writer
├── cost/                      Per-model token pricing and cost attribution
├── eval/                      LLM judge, regression, benchmarks, worker
│   ├── batch_scorer.py        Parallel scoring with asyncio semaphore
│   ├── calibration.py         Cohen's kappa vs human labels
│   ├── meta_eval.py           Judge-the-judge auditor
│   └── statistics.py          Bootstrap CI, Cohen's d, significance
├── instrumentation/           OTel tracer + LangGraph instrumentor
├── multiagent/                Multi-agent group eval (roles, protocols, metrics)
├── remediation/               Retry, tool swap, escalate strategies
├── storage/                   SQLAlchemy models, partitioning, telemetry service
└── watchdog/                  FailureDetector, LLM judge, detectors/

dashboard/                     Next.js App Router UI
sdk-js/                        JavaScript instrumentation SDK
mcp_server/                    FastMCP server exposing tools
config/                        eval datasets, fallback tools, Grafana dashboards
migrations/                    Alembic migrations
tests/                         pytest unit + integration suites
```

## API Docs

With the API running, OpenAPI + Swagger UI live at `http://localhost:8000/docs`.

## Development

### Run tests

```bash
pytest                         # all tests
pytest tests/unit -q           # unit only
pytest -k judge                # filter by name
```

### Lint and format

```bash
ruff check .
ruff format .
```

### Run a local regression

```bash
python -m otelmind.eval.worker   # starts eval, autoscorer, daily golden loops
```

### Dashboard

```bash
cd dashboard
npm install
npm run dev                    # http://localhost:3000
```

## Deployment

See `DEPLOY.md` for the full runbook — Railway (backend), Vercel (dashboard), Neon (Postgres).

## License

MIT
