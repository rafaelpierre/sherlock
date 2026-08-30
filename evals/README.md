# Sherlock evaluations

The shared evaluation runner loads versioned JSON suites from `evals/cases`,
executes all or a selected subset, prints a concise summary, and writes a
machine-readable JSON report.

From a clean checkout, run the deterministic smoke suite without credentials or
network access:

```bash
cd backend
uv sync --locked --dev
uv run sherlock-eval --suite runner-smoke
```

The default report is `eval-results/report.json`, which is ignored by Git. Use
`--output PATH` to retain a named artifact and `--dataset-revision LABEL` when
evaluating a dataset other than the bundled SQLite file. Without an explicit
label, the runner records a content hash of the bundled dataset.

## Suite fixture contract

Every `evals/cases/*.json` file has this shape:

```json
{
  "schema_version": 1,
  "name": "unique-suite-name",
  "description": "What this suite establishes.",
  "kind": "text2sql",
  "cases": [
    {
      "id": "unique-case-id",
      "prompt": "Natural-language input",
      "expected": {"field": "expected value"},
      "fixture_response": {"field": "expected value"}
    }
  ]
}
```

`kind` is `text2sql` or `rule_generation`. The runner recursively compares the
fields in `expected` with the service response; extra response fields are
allowed. Lists remain order-sensitive in schema version 1. The future domain
suites may extend their comparison policies without changing the report
contract. `fixture_response` drives deterministic offline runs and is required
unless `--live` is used.

Suite names and case IDs use lowercase letters, digits, hyphens, and underscores.
Names must be unique across files, and case IDs must be unique within a suite.
Malformed JSON, unsupported schema versions, duplicate names, and unknown suite
selections are configuration errors.

## Live Bedrock execution

Live execution is always opt-in and is never an ordinary PR quality gate:

```bash
cd backend
AWS_PROFILE=my-profile AWS_REGION=eu-west-2 BEDROCK_MODEL_ID=your-model-id \
uv run sherlock-eval \
  --live \
  --model "$BEDROCK_MODEL_ID" \
  --suite runner-smoke \
  --dataset-revision fraud-data-2026-08-30 \
  --output ../eval-results/live-smoke.json
```

Configure AWS credentials, region, Bedrock model access, and the MCP transport as
described in `backend/README.md`. The `--model` value is passed directly to each
Strands agent and recorded in the report. Do not place credentials, tokens, or
secret-bearing configuration in suite fixtures or revision labels.

Live results are not perfectly reproducible: managed model versions, provider
sampling, service changes, and latency can vary even when the model identifier,
dataset revision, and local configuration match. Reports therefore record the
UTC run date, mode, model, safe runner configuration, dataset revision, case
latency, and repair metadata.

`repair_count` is the number of repair attempts for a case. `repair_rate` is the
fraction of non-error cases that required at least one repair.

## Report and exit behavior

Reports conform to [`schemas/report.schema.json`](schemas/report.schema.json).
Each case is `passed`, `failed` (the response did not match its oracle), or
`error` (execution raised an exception). A failing case does not stop later
cases, so partial runs still produce a complete report and summary.

Exit codes are stable:

| Code | Meaning |
|---:|---|
| `0` | Every selected case passed and the report was written. |
| `1` | Execution completed, but at least one case failed or errored. |
| `2` | Arguments, fixtures, live configuration, or report writing were invalid. |

The report intentionally contains case outputs. Treat committed live artifacts
as potentially sensitive, review them before sharing, and never include raw
credentials.
