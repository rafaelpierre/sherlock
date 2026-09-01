# Sherlock backend

The backend is a Python 3.13 FastAPI application that coordinates the FSM
investigation workflow. It uses Strands Agents and Amazon Bedrock for language
tasks, but it never opens SQLite directly: all schema inspection and query
execution cross the MCP boundary. See the [root architecture guide](../README.md)
for the end-to-end design.

## Responsibilities and boundaries

- Create a fresh `ChatAgent` for every `/v1/chat` request; keep no hidden
  conversation or investigation state between requests.
- Select exactly one workflow per turn: `EXPLORE`, `GENERATE_RULE`,
  `REFINE_RULE`, `BACKTEST_RULE`, or `COMPARE_RULES`.
- Hand an exploration once to a fresh bounded `AnalysisAgent`; it can make
  sequential evidence requests only through `Text2SQLService`.
- Generate/refine candidate predicates, validate them deterministically, and
  calculate backtest/comparison metrics deterministically.
- Translate native agent activity into the safe typed HTTP/SSE contract.
- Export safe OpenTelemetry trace metadata to Arize Phoenix when configured.

The browser supplies at most 20 history messages and explicit working state.
The terminal `complete` response replaces that state. Missing state—for example
“Backtest it” without a rule—is a structured `422`, not an invented rule.

## Run locally

Requirements: Python 3.13+, `uv`, AWS credentials/region, and Bedrock model
access.

```bash
uv sync --locked --dev
SHERLOCK_AUTH_REQUIRED=false uv run backend-api
```

Readiness is `GET http://localhost:8080/v1/health`; it does not call Bedrock.
The default MCP transport is stdio, so the backend starts `../mcp` itself:

```bash
uv run backend "Which card type has the highest fraud rate?"
```

For the full supported stack, run `docker compose up --build --wait` from the
repository root. Compose sets Streamable HTTP MCP at `http://mcp:8000/mcp`.

## Authentication

Authentication is enabled by default. When `SHERLOCK_AUTH_REQUIRED=true`, both
`SHERLOCK_COGNITO_ISSUER` and `SHERLOCK_COGNITO_CLIENT_ID` are required; every
product endpoint requires a Cognito **access** token in `Authorization: Bearer
<token>`. The HTTP boundary validates the token's RS256 signature from issuer
JWKS, issuer, expiry, `token_use`, and `client_id` before request parsing or
workflow creation. Missing or invalid credentials receive the safe `401`
response with `WWW-Authenticate: Bearer`. `GET /v1/health` is intentionally
unauthenticated for platform health checks.

Compose opts out with `SHERLOCK_AUTH_REQUIRED=false` for local Bedrock/MCP
development. A deployed environment must configure issuer and client ID together
and must not disable authentication; do not commit tokens or client secrets.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /v1/health` | Process readiness only. |
| `POST /v1/query` | Direct Text2SQL request; returns normalized SQL, a bounded table, repair count, and cache status. |
| `POST /v1/chat` | Orchestrated FSM turn; returns JSON, or SSE when `Accept: text/event-stream` is requested. |
| `POST /v1/rules/generate` | Generate then validate a candidate rule. |
| `POST /v1/rules/refine` | Refine an explicit rule; invalid current rule is `422`. |
| `POST /v1/rules/backtest` | Validate and replay an explicit candidate rule against historical data. |
| `POST /v1/rules/compare` | Compare two explicit candidate rules using their historical metrics. |

Chat SSE contains only `text_delta`, `tool_call`, `tool_result`, `complete`,
and `error`. `complete` is authoritative for artifacts, metadata, and working
state. Provider events, reasoning, raw tool arguments/results, and unrestricted
rows never reach the browser.

Delivery from the workflow producer to the SSE writer is bounded to 20 public
events and 256 KiB. When the event queue is full, the producer waits for the
writer to drain it; ordinary bursts are not discarded or converted into a
`StreamBufferFull` error. A single event over the byte bound follows the safe
SSE error path. Client disconnects, cancellation, and the workflow deadline
close the stream and release the associated work.

Example direct query:

```bash
curl -X POST http://localhost:8080/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which card type has the highest fraud rate?"}'
```

## Agent and service design

`Text2SQLService` creates an isolated SQL-generation agent with access to
schema/sample inspection. Deterministic service code calls `run_query`; a
generation agent cannot execute arbitrary database operations. Repairable MCP
errors are returned to a fresh generator for at most two corrections. Initial
translations may be served from a bounded process-local LRU cache, but cached
SQL is still validated and executed through MCP.

An `AnalysisAgent` can adapt an investigation to its previous completed
evidence. The implementation enforces no more than five attempted queries,
seven model turns, 240 seconds wall-clock time, and 140,000 JSON characters of
grouped evidence. Each successful step is an ordered `analysis_step` artifact:
the public question, SQL, and bounded table. Partial failure is qualified; an
all-failed analysis uses a controlled error boundary.

Rules are predicates over `fraud_transactions`. The validator rejects unsafe
syntax, unknown fields, subqueries, comments, and `is_fraud`; backtesting
validates again. Labelled rows alone drive precision, recall, false-positive,
and fraud-value quality metrics. Alert volume includes all rows and separately
reports unlabelled flagged transactions.

## MCP transport configuration

| Variable | Default | Meaning |
|---|---|---|
| `SHERLOCK_MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http`, or `agentcore` |
| `SHERLOCK_MCP_URL` | `http://localhost:8000/mcp` | Streamable HTTP endpoint |
| `SHERLOCK_MCP_STDIO_COMMAND` | `uv` | Child-process command |
| `SHERLOCK_MCP_STDIO_ARGS` | sibling MCP command | JSON array of arguments |
| `SHERLOCK_MCP_HTTP_HEADERS` | unset | JSON object of HTTP headers |
| `SHERLOCK_MCP_STARTUP_TIMEOUT` | `30` | Startup timeout in seconds |
| `SHERLOCK_AGENTCORE_RUNTIME_ARN` | unset | Required for `agentcore` transport |
| `SHERLOCK_AUTH_REQUIRED` | `true` | Require Cognito authentication for product routes |
| `SHERLOCK_COGNITO_ISSUER` | unset | Cognito user-pool issuer; required with authentication |
| `SHERLOCK_COGNITO_CLIENT_ID` | unset | Cognito app-client ID; required with authentication |
| `SHERLOCK_WORKFLOW_DEADLINE_SECONDS` | `240` | End-to-end per-worker workflow deadline |
| `SHERLOCK_MODEL_IN_FLIGHT_LIMIT` | `8` | Model-capable workflow permits per worker |
| `SHERLOCK_MCP_IN_FLIGHT_LIMIT` | `16` | MCP-backed workflow permits per worker |

In the delivered AWS deployment, `agentcore` transport constructs the AgentCore
runtime invocation URL and SigV4-signs it with the ECS task role. Do not put AWS
keys in application configuration or images.

Workers admit model/MCP work without an unbounded local queue. An overloaded
request returns `503` with `Retry-After`; a deadline returns `504` for JSON or a
safe `error` SSE event after streaming begins. Cancellation releases permits and
propagates downstream where the provider supports it. The bounded SSE delivery
queue applies backpressure independently of this admission control. Workflow
telemetry records only class, outcome, and elapsed time—not prompts, SQL, rows,
or reasoning.

## Phoenix tracing

`POST /v1/chat` creates a root `sherlock.chat.turn` span for the complete JSON
or streamed turn. Child workflow and Strands spans inherit its context. The
backend deliberately excludes messages, prompts, SQL, transaction rows, raw
tool data, credentials, and provider reasoning from its attributes.

Set `SHERLOCK_PHOENIX_SECRET_ID` to an AWS Secrets Manager ARN whose JSON has
`PHOENIX_API_KEY` and `PHOENIX_ENDPOINT` (an HTTPS URL ending `/v1/traces`). At
startup the service configures OTLP/HTTP and an `Authorization=Bearer` header.
The deployed service supplies a 5-second batch delay, 512-span batches, a
2,048-span queue, 10-second export timeouts, and parent-based sampling. Missing,
malformed, or unreadable secrets disable exporting without preventing startup.

## Evaluation and checks

The deterministic runner avoids Bedrock and the network by default:

```bash
uv run sherlock-eval --suite runner-smoke
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run complexipy . --max-complexity-allowed 15 --failed --color no
uv run pytest --cov=sherlock --cov-report=term-missing --cov-fail-under=80
```

Live evaluation requires both `--live` and an explicit `--model`. The current
smoke result validates runner/report mechanics, not model quality; see
[`../evals/README.md`](../evals/README.md).
