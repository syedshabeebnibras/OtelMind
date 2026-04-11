# Golden eval datasets

Files in this directory are read by
`otelmind/eval/worker.py::daily_golden_regression_loop`.

The active file is configured via `EVAL_GOLDEN_DATASET_PATH` (default
`config/eval_datasets/golden.yaml`).

## Format

```yaml
cases:
  - id: "unique-case-id"          # used to match baseline ↔ candidate
    question: "..."               # input prompt
    expected: "..."               # ground-truth answer
    candidate_actual: "..."       # OPTIONAL — the current prompt's output
    context: "..."                # OPTIONAL — source text for faithfulness
```

`candidate_actual` is the field CI updates nightly. If you omit it, the
daily loop will compare "expected vs expected" and never report a
regression — the loop is effectively a no-op until a CI job pre-runs
the current agent and writes its outputs into this field.

## Daily flow

1. A CI job runs the current production prompt against each `question`.
2. Captured outputs are written back into `candidate_actual` and the
   file is committed.
3. The API picks up the change automatically — it reads the file on
   each daily tick, no restart required.
4. Worker loop scores the pairs via `LLMJudge`, records a new
   `EvalRun` named `daily-golden-YYYY-MM-DD`, and compares its
   per-dimension means against the previous day's run.
5. If any dimension dropped more than `EVAL_REGRESSION_THRESHOLD`
   (default 5%), a `FailureClassification(failure_type="eval_regression")`
   is written and the watchdog alerting pipeline routes it to the
   configured channels.

## Dimensions scored

`faithfulness`, `relevance`, `coherence`, `safety`, `tool_use_accuracy`
— see `otelmind/eval/judge.py::DIMENSIONS` and their prompt templates
for exactly what each dimension penalizes.
