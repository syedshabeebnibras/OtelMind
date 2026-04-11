# OtelMind MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that exposes
**OtelMind's AI agent failure detection and evaluation** capabilities as tools that
Claude (or any MCP client) can call directly.

All four tools work **without an API key** using heuristic rules. Setting
`OPENAI_API_KEY` unlocks GPT-4o-powered semantic analysis.

---

## What it does

| Tool | Description | Needs API key? |
|------|-------------|---------------|
| `classify_agent_failure` | Classify failures in a LangGraph/agent trace | No (heuristic) / Optional (LLM) |
| `check_hallucination` | Check if an LLM output is grounded in source context | No (keyword overlap) / Optional (GPT-4o) |
| `run_eval_benchmark` | Score test cases: accuracy, faithfulness, relevance | No (accuracy only) / Optional (all) |
| `get_trace_summary` | Duration, tokens, cost, bottlenecks, timeline | Never |

---

## How it works

```
                     Input Trace (list of spans)
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Heuristic Rules   │  ← always runs, free
                    │  ┌───────────────┐  │
                    │  │ tool_timeout  │  │  span > 30s
                    │  │ infinite_loop │  │  same node ≥ 5×
                    │  │ ctx_overflow  │  │  tokens > 120k
                    │  │ tool_misuse   │  │  ≥ 2 ERROR spans
                    │  └───────────────┘  │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
           Match found                  No match
                 │                           │
                 ▼                           ▼
         Return result              OPENAI_API_KEY set?
         (fast, free)              ┌──────────┴──────────┐
                                   │                     │
                                  Yes                    No
                                   │                     │
                                   ▼                     ▼
                             LLM Judge             Return
                             (GPT-4o)            no_failure
                             semantic
                             analysis
                                   │
                           confidence ≥ 0.7?
                          ┌────────┴────────┐
                          │                 │
                         Yes               No
                          │                 │
                          ▼                 ▼
                    Return LLM         Return
                  classification      no_failure
```

---

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Option A — uv (recommended)

```bash
cd mcp_server
uv sync
```

### Option B — pip

```bash
cd mcp_server
pip install -e .
```

### With LLM features

```bash
export OPENAI_API_KEY="sk-..."
```

---

## Running the server

```bash
# From the mcp_server/ directory:
python server.py

# Or via the installed script:
otelmind-mcp
```

---

## Claude Desktop configuration

Add this to your `claude_desktop_config.json`
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "otelmind": {
      "command": "python",
      "args": ["/absolute/path/to/OtelMind/mcp_server/server.py"],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

Or with uv:

```json
{
  "mcpServers": {
    "otelmind": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/absolute/path/to/OtelMind/mcp_server",
        "server.py"
      ],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

---

## Tool reference

### `classify_agent_failure`

Classify failures in an AI agent trace.

**Input:**
```json
{
  "trace": [
    {
      "span_name": "call_model",
      "duration_ms": 1200,
      "status_code": "OK",
      "prompt_tokens": 850,
      "completion_tokens": 120,
      "input_preview": "What is the capital of France?",
      "output_preview": "The capital of France is Paris."
    },
    {
      "span_name": "search_tool",
      "duration_ms": 45000,
      "status_code": "ERROR",
      "error_message": "Connection timeout after 45s"
    }
  ]
}
```

**Output (heuristic — no API key needed):**
```json
{
  "failure_type": "tool_timeout",
  "confidence": 0.75,
  "judge_model": "heuristic",
  "reasoning": "Span 'search_tool' took 45000ms (threshold: 30000ms)",
  "span_id": null
}
```

**Output (LLM mode — with OPENAI_API_KEY):**
```json
{
  "failure_type": "hallucination",
  "confidence": 0.87,
  "judge_model": "gpt-4o",
  "reasoning": "The agent asserted facts not present in the retrieved context."
}
```

**Possible `failure_type` values:**
`hallucination` | `tool_timeout` | `infinite_loop` | `context_overflow` | `tool_misuse` | `no_failure`

---

### `check_hallucination`

Check whether an LLM output is grounded in the provided source context.

**Input:**
```json
{
  "llm_output": "The Eiffel Tower was built in 1887 and stands 330 metres tall.",
  "source_context": "The Eiffel Tower construction began in 1887 and was completed in 1889. It stands 300 metres tall (330 metres including the antenna)."
}
```

**Output (keyword-overlap heuristic — no API key):**
```json
{
  "is_grounded": true,
  "confidence": 0.82,
  "reasoning": "Keyword overlap between output and context: 68.4%. Output shares sufficient vocabulary with context.",
  "unsupported_claims": [],
  "method": "keyword_overlap",
  "overlap_score": 0.684
}
```

**Output (LLM mode — with OPENAI_API_KEY):**
```json
{
  "is_grounded": false,
  "confidence": 0.91,
  "reasoning": "The output states the tower is 330 metres tall without the antenna qualification present in the source.",
  "unsupported_claims": ["stands 330 metres tall"],
  "method": "llm_judge"
}
```

---

### `run_eval_benchmark`

Score a set of LLM test cases across evaluation metrics.

**Input:**
```json
{
  "test_cases": [
    {
      "input": "What is 2 + 2?",
      "expected": "4",
      "actual": "The answer is 4."
    },
    {
      "input": "Summarize quantum entanglement in one sentence.",
      "expected": "Two particles remain correlated regardless of distance.",
      "actual": "Quantum entanglement links particles so measuring one instantly affects the other."
    }
  ],
  "metrics": ["accuracy", "faithfulness", "relevance"]
}
```

**Output:**
```json
{
  "summary": {
    "accuracy": { "mean": 0.7241, "min": 0.5714, "max": 0.8768, "scored": 2, "total": 2 },
    "faithfulness": { "mean": 0.875, "min": 0.8, "max": 0.95, "scored": 2, "total": 2 },
    "relevance": { "mean": 0.9, "min": 0.85, "max": 0.95, "scored": 2, "total": 2 }
  },
  "per_case": [
    {
      "input": "What is 2 + 2?",
      "expected": "4",
      "actual": "The answer is 4.",
      "scores": {
        "accuracy": 0.5714,
        "faithfulness": 0.95,
        "faithfulness_reasoning": "The actual answer correctly states the value 4.",
        "relevance": 0.95,
        "relevance_reasoning": "Directly answers the arithmetic question."
      }
    }
  ],
  "total_cases": 2,
  "metrics": ["accuracy", "faithfulness", "relevance"],
  "llm_scoring": true
}
```

---

### `get_trace_summary`

Summarise an agent trace: duration, tokens, cost, bottlenecks, and timeline.

**Input:**
```json
{
  "trace": [
    {
      "span_name": "agent_start",
      "duration_ms": 5,
      "status_code": "OK",
      "start_time": 1712000000.0
    },
    {
      "span_name": "call_model",
      "duration_ms": 1850,
      "status_code": "OK",
      "prompt_tokens": 1200,
      "completion_tokens": 340,
      "model": "gpt-4o",
      "start_time": 1712000000.005
    },
    {
      "span_name": "search_tool",
      "duration_ms": 620,
      "status_code": "OK",
      "start_time": 1712000001.855
    },
    {
      "span_name": "call_model",
      "duration_ms": 990,
      "status_code": "OK",
      "prompt_tokens": 1450,
      "completion_tokens": 180,
      "model": "gpt-4o",
      "start_time": 1712000002.475
    }
  ]
}
```

**Output:**
```json
{
  "span_count": 4,
  "total_duration_ms": 3465,
  "token_usage": {
    "prompt": 2650,
    "completion": 520,
    "total": 3170
  },
  "cost_estimate_usd": 0.021,
  "model": "gpt-4o",
  "bottlenecks": [
    {
      "span_name": "call_model",
      "duration_ms": 1850,
      "pct_of_total": 53.4,
      "span_id": null
    }
  ],
  "repeated_nodes": [
    { "node": "call_model", "count": 2 }
  ],
  "error_details": [],
  "timeline": [
    { "index": 0, "span_name": "agent_start",  "duration_ms": 5,    "status": "OK", "prompt_tokens": null, "completion_tokens": null, "error": null },
    { "index": 1, "span_name": "call_model",   "duration_ms": 1850, "status": "OK", "prompt_tokens": 1200, "completion_tokens": 340, "error": null },
    { "index": 2, "span_name": "search_tool",  "duration_ms": 620,  "status": "OK", "prompt_tokens": null, "completion_tokens": null, "error": null },
    { "index": 3, "span_name": "call_model",   "duration_ms": 990,  "status": "OK", "prompt_tokens": 1450, "completion_tokens": 180, "error": null }
  ]
}
```

---

## Span format reference

The trace tools accept a flexible span format. Supported fields:

| Field | Type | Notes |
|-------|------|-------|
| `span_name` or `name` | str | Node / tool name |
| `duration_ms` | float | Execution time in milliseconds |
| `status_code` | str | `"OK"` \| `"ERROR"` \| `"UNSET"` |
| `prompt_tokens` | int | LLM input token count |
| `completion_tokens` | int | LLM output token count |
| `model` | str | LLM model name (for cost estimation) |
| `error_message` | str | Error details if status is ERROR |
| `span_id` | str | Unique span identifier |
| `start_time` | float | Unix timestamp (for timeline ordering) |
| `input_preview` | str | Truncated input shown to LLM judge |
| `output_preview` | str | Truncated output shown to LLM judge |
| `node` | str | Alternative to `span_name` for LangGraph nodes |

All fields are optional except `span_name`/`name`.

---

## Project structure

```
mcp_server/
├── server.py              # FastMCP server entry point
├── tools/
│   ├── classifier.py      # classify_agent_failure implementation
│   ├── hallucination.py   # check_hallucination implementation
│   ├── eval_runner.py     # run_eval_benchmark implementation
│   └── trace_summary.py   # get_trace_summary implementation
├── pyproject.toml
├── README.md
└── .gitignore
```

The tools are intentionally self-contained and do not import from the parent
`otelmind` package, so the MCP server can be installed and run independently.
