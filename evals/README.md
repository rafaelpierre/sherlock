# Sherlock evaluations

## Package quality checks

Changes under `evals/` run the path-scoped `Evals CI` workflow. Reproduce its
locked lint, format, test, and coverage checks locally with:

```bash
cd evals
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=sherlock_evals --cov-report=term-missing \
  --cov-fail-under=80
```

## Text2SQL HTTP evaluation

The Text2SQL CLI sends the questions in `data/text2sql.json` to a running
Sherlock backend through `POST /v1/query`. A case passes only when the endpoint
returns one safe, read-only query and its normalized columns and rows match the
committed deterministic oracle. The evaluator compares result sets rather than
generated SQL strings, so different valid SQL can produce the same passing
answer.

Start the backend, then run the suite from a second terminal:

```bash
cd backend
uv run backend-api
```

```bash
cd evals
uv sync --locked --dev
uv run sherlock-text2sql-eval --output eval-results/text2sql-report.json
```

Use `--base-url` or `SHERLOCK_BACKEND_URL` to target a different backend. Use
`--cases` to run a different schema-version-2 JSON file, `--timeout` to change
the per-request timeout, and `--output` to persist the case-level and aggregate
machine-readable report. The command runs every case, prints a pass/fail line
and summary, and exits with status 1 when any case fails.

Failures are classified independently as `http`, `execution`, `safety`, or
`semantic`. Semantic accuracy uses only cases that reached result comparison;
transport, malformed response, truncated result, and unsafe-SQL failures do not
inflate or reduce that denominator. Reports include expected and actual result
sets for diagnosis and should be reviewed before sharing.

## Text2SQL oracle contract

Each committed case has `expected.columns`, `expected.rows`, and an explicit
`comparison` object:

```json
{
  "id": "example",
  "question": "A natural-language analytics question",
  "expected": {
    "columns": ["category", "fraud_rate_percent"],
    "rows": [["example", 0.25]]
  },
  "comparison": {
    "row_order": "insensitive",
    "column_order": "insensitive",
    "absolute_tolerance": 0.0001,
    "relative_tolerance": 0.000001,
    "null_equivalents": []
  }
}
```

Column names are stripped, whitespace-normalized, and case-folded. An
order-insensitive column policy reorders row values by their normalized column
name before comparison. Rows are compared as a multiset when row order is
insensitive, preserving duplicate counts. Ranked policies additionally name
ordered columns and sort directions, while allowing rows tied on those keys in
either order. Numeric tolerances use the usual absolute-or-relative closeness
rule. JSON `null` is exact by default; configured case-insensitive string tokens
such as `"n/a"` may be treated as null.

The committed values are calculated from the bundled
`mcp/db/data/data.db` snapshot through the canonical `fraud_transactions` view.
Fraud-rate denominators contain labelled rows only (`is_fraud IS NOT NULL`),
while the unlabelled case counts `is_fraud IS NULL` explicitly. Ranked lists are
top 10 unless fewer than 10 groups contain qualifying fraudulent rows. Ratio,
amount, and income distributions use the band labels committed in their
expected rows; monthly and weekday series use chronological order. These
definitions make otherwise open-ended questions deterministic without allowing
outcome-null rows to be treated as non-fraud.

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
