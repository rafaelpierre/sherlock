# Sherlock Text2SQL Agent

A small [Strands Agents](https://strandsagents.com/) application that answers
natural-language fraud analytics questions through the MCP server in `../mcp`.
The backend never opens SQLite directly: Strands discovers the MCP tools and the
MCP server validates and executes the generated read-only SQL.

## Run the HTTP API

Start the FastAPI application locally:

```bash
cd backend
uv sync
uv run backend-api
```

The readiness endpoint is `GET http://localhost:8080/v1/health`. It reports API
process readiness without invoking Bedrock or running an analytical query.

## Authentication

When both `SHERLOCK_COGNITO_ISSUER` and `SHERLOCK_COGNITO_CLIENT_ID` are set,
all `/v1` product endpoints require a Cognito **access** token in an
`Authorization: Bearer <token>` header. The backend validates the JWT's RS256
signature against the issuer JWKS, issuer, expiry, `token_use`, and `client_id`
before creating a workflow or parsing a product request. Missing or invalid
tokens receive the safe `401` response `{"detail":"Authentication is required."}`
with `WWW-Authenticate: Bearer`. `/v1/health` remains unauthenticated for
platform health checks.

Authentication is enabled by default. The issuer and client ID must be
configured together whenever `SHERLOCK_AUTH_REQUIRED=true`. Local development
must explicitly set `SHERLOCK_AUTH_REQUIRED=false`; deployed environments must
supply both Cognito values and must not disable authentication.

Submit a standalone analytics question directly to the Text2SQL service:

```bash
curl -X POST http://localhost:8080/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which card type has the highest fraud rate?"}'
```

The response includes the normalized SQL, tabular result, generation attempt
count, and whether the initial natural-language-to-SQL translation was served
from the process-local cache. Generated and returned SQL is limited to 20,000
characters so every successful query can be retained as bounded
`working_state.last_sql` in a later chat request. Model output above that limit
is rejected before MCP execution and `/v1/query` returns a controlled `502`.
For direct rule refinement, an invalid current rule returns its validation
details as `422`; a schema or query dependency failure returns the retryable
`502` payload `{"detail": "..."}`.

## Use the conversational API

`POST /v1/chat` creates a fresh ChatAgent for each request. An `EXPLORE` turn
hands off once to a fresh AnalysisAgent, which can run adaptive sequential
questions through the existing Text2SQL service. The browser supplies bounded
recent history and explicit working state; the backend does not retain hidden
conversation or investigation state between requests.

```bash
curl -X POST http://localhost:8080/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id":"3b621bd5-98dd-4be0-b713-89b1ac751fab",
    "message":"Which card type has the highest fraud rate?",
    "history":[],
    "working_state":{}
  }'
```

Each response contains assistant text, typed artifacts, authoritative replacement
working state, and intent/repair/cache metadata. Supported workflows are data
exploration, candidate-rule generation and refinement, historical backtesting,
and current-versus-previous rule comparison. State-dependent requests such as
`Backtest it` return a structured `422` response when the required rule is absent.

Analysis is bounded to five attempted Text2SQL questions, seven Strands model
turns, and a 240-second wall-clock deadline. Narrow questions may stop after one
successful query. Broad questions return each successful step in deterministic
order as one `analysis_step` artifact containing its question, SQL, and bounded
table. Failed steps consume the query budget; after partial success the agent may
recover or explicitly qualify the missing evidence, while an all-failed analysis
uses the controlled ChatAgent error boundary.

The aggregate grouped evidence is capped at 140,000 JSON characters so the
terminal SSE response remains within the browser's bounded event parser. A query
that would exceed this cap is treated as unavailable evidence; earlier successful
steps remain available for a qualified synthesis.

For `EXPLORE`, `repair_count` is the sum of Text2SQL repairs across successful
steps and `cache_hit` is true only when every successful step used cached initial
SQL. `working_state.last_sql` is the most recently successful statement. Raw
result rows remain confined to response artifacts and are not added to working
state or conversation history.

Clients that send `Accept: text/event-stream` receive named SSE events in this
order:

- `text_delta` carries an explicit `segment`: buffered pre-tool text is an
  `introduction`, while `content` appends the result summary. For `EXPLORE`,
  coordinator text after the handoff is discarded so only the specialist owns
  the closing synthesis;
- `tool_call` starts a user-facing activity or specialist handoff;
- `tool_result` finishes that activity using the same bounded activity ID and
  an explicit success/failure outcome;
- later `text_delta` events append the result summary and any closing follow-up
  or clarification question (the terminal specialist synthesis for `EXPLORE`);
- `complete` supplies the authoritative `ChatResponse`; or
- `error` supplies a bounded, user-safe message if the stream cannot complete.

The backend translates native Strands lifecycle events into this stable product
contract. Provider payloads, reasoning content, raw tool arguments, internal tool
names, and raw tool results are never sent to the browser. Only `complete` should
be used to commit artifacts, metadata, or replacement working state.

Client-owned history may include up to 20 messages, with up to 50 user-safe
activity summaries on each assistant turn. These summaries preserve
interrupted-turn continuity for a later request, but are formatted as labelled
conversational context rather than reconstructed native tool calls or
authoritative data.

```bash
curl -N -X POST http://localhost:8080/v1/chat \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id":"3b621bd5-98dd-4be0-b713-89b1ac751fab",
    "message":"Which card type has the highest fraud rate?",
    "history":[],
    "working_state":{}
  }'
```

Clients that do not request SSE continue to receive the JSON response and HTTP
error contract shown above.

## Execution budgets

Each backend worker applies an end-to-end budget to `/v1/query`, candidate-rule
generation/refinement, backtesting/comparison, and chat (including SSE). The
same deadline covers MCP client startup, model calls, validation, and bounded
repair work. A worker admits work without a local wait queue: model-capable
work reserves both a model and MCP permit, while deterministic backtesting and
comparison reserve an MCP permit. Excess work returns `503` with `Retry-After`;
expired JSON work returns `504`, while an already-open SSE response emits a
safe `error` event. Client cancellation releases local permits and propagates
cancellation to downstream async work where the provider supports it.

Defaults can be changed per worker with these validated environment variables:

- `SHERLOCK_WORKFLOW_DEADLINE_SECONDS=240`
- `SHERLOCK_MODEL_IN_FLIGHT_LIMIT=8`
- `SHERLOCK_MCP_IN_FLIGHT_LIMIT=16`

The backend records only workflow class, outcome class, and elapsed time for
these controls; it never logs prompts, SQL, transaction rows, tool arguments,
or model reasoning.

## Run locally with stdio

The default transport is stdio. The backend starts the sibling MCP project as a
child process, so a separate server does not need to be running:

```bash
cd backend
uv sync
uv run backend "Which card type has the highest fraud rate?"
```

Strands uses Amazon Bedrock by default, so configure AWS credentials, region,
and model access as required by your chosen Strands model provider.

The local command is equivalent to this MCP client configuration:

```json
{
  "transport": "stdio",
  "command": "uv",
  "args": ["--directory", "../mcp", "run", "fraud-mcp-stdio"]
}
```

The actual default MCP path is resolved to an absolute path, so the command is
independent of the shell's current directory.

## Switch to Streamable HTTP

Start the MCP server separately (or run its Docker image), then change only the
backend environment:

```bash
SHERLOCK_MCP_TRANSPORT=streamable-http \
SHERLOCK_MCP_URL=http://localhost:8000/mcp \
uv run backend "Show monthly fraud rates for the last year in the database"
```

In Docker or AWS, set `SHERLOCK_MCP_URL` to the MCP service's private reachable
URL, for example `http://fraud-mcp:8000/mcp`. Do not use `0.0.0.0` as a client
address.

## Docker

Build the independently runnable backend image from this directory:

```bash
docker build -t sherlock-backend .
docker run --rm -p 8080:8080 \
  --add-host host.docker.internal=host-gateway \
  -e SHERLOCK_MCP_TRANSPORT=streamable-http \
  -e SHERLOCK_MCP_URL=http://host.docker.internal:8000/mcp \
  -v ~/.aws:/run/sherlock-aws:ro \
  sherlock-backend
```

For the supported full-stack workflow, run `docker compose up --build --wait`
from the repository root. Compose waits for MCP readiness and supplies its
private URL automatically. At startup, profile files are copied from the
read-only mount into the container with permissions for the unprivileged app
user; AWS credentials remain outside the image.

## Backend configuration

| Variable | Default | Purpose |
|---|---|---|
| `SHERLOCK_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `SHERLOCK_MCP_URL` | `http://localhost:8000/mcp` | Streamable HTTP endpoint |
| `SHERLOCK_MCP_STDIO_COMMAND` | `uv` | Local MCP subprocess command |
| `SHERLOCK_MCP_STDIO_ARGS` | local sibling MCP command | JSON array of command arguments |
| `SHERLOCK_MCP_HTTP_HEADERS` | unset | JSON object of HTTP headers |
| `SHERLOCK_MCP_STARTUP_TIMEOUT` | `30` | MCP initialization timeout in seconds |
| `SHERLOCK_WORKFLOW_DEADLINE_SECONDS` | `240` | End-to-end workflow deadline in seconds |
| `SHERLOCK_MODEL_IN_FLIGHT_LIMIT` | `8` | Maximum model-capable workflows per worker |
| `SHERLOCK_MCP_IN_FLIGHT_LIMIT` | `16` | Maximum MCP-backed workflows per worker |
| `SHERLOCK_PHOENIX_SECRET_ID` | unset | AWS Secrets Manager ARN containing Phoenix OTLP credentials |

## Phoenix OpenTelemetry tracing

The backend emits one root `sherlock.chat.turn` span for every `POST /v1/chat`
turn (including the complete SSE lifetime). Strands agent/model/tool spans and
Sherlock's workflow spans inherit that trace context. User messages, prompts,
raw SQL, tool arguments/results, transaction rows, credentials, and provider
reasoning are never added by Sherlock; Strands sensitive GenAI attributes are
redacted with `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=`.

Production credentials are kept only in AWS Secrets Manager. After Terraform
has created `sherlock-phoenix-otel`, add these GitHub Actions secrets and run
the **Sync Phoenix OpenTelemetry secret** workflow once from `main`:

- `PHOENIX_API_KEY`: the Phoenix API key;
- `PHOENIX_ENDPOINT`: the complete HTTPS OTLP traces endpoint, ending in
  `/v1/traces`.

The workflow stores a JSON secret with those two keys. At startup the backend
sets `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and
`OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <key>` from that secret, then
uses the standard OTLP/HTTP exporter. The task definition supplies bounded
batch processing (5 second delay, 512-span batches, 2,048-span queue), 10
second export timeouts, parent-based sampling, and HTTP/protobuf. The Python
OTLP exporter performs its built-in bounded exponential retry behaviour.
Missing, malformed, or unreadable credentials disable exporting rather than
changing any API response or preventing startup.

For example, a custom local checkout can use:

```bash
SHERLOCK_MCP_STDIO_ARGS='["--directory","/work/mcp","run","fraud-mcp-stdio"]' \
uv run backend "How many transactions are unlabelled?"
```

Keep credentials in the environment or a secret manager; do not commit bearer
tokens in header configuration. For a public AWS endpoint, add authentication
and TLS at the MCP service or reverse-proxy layer.

## Run evaluations

The shared evaluation runner defaults to committed deterministic responses, so
ordinary development and CI do not call Bedrock or the network:

```bash
uv run sherlock-eval --suite runner-smoke
```

It writes a versioned machine-readable report to `../eval-results/report.json`
and returns a nonzero status for case failures or configuration errors. Live
model execution requires both `--live` and an explicit `--model`. See
[`../evals/README.md`](../evals/README.md) for fixture and report schemas,
reproducibility metadata, credentials, limitations, and exit codes.

## How Text2SQL works

The API route only validates HTTP input and delegates to `Text2SQLService`. The
service asks an isolated SQL-generation agent to inspect `get_schema`, optionally
inspect bounded values with `get_sample_values`, and produce one structured
SQLite `SELECT`/`WITH` statement. The agent cannot call `run_query`.

The service then calls `run_query` deterministically through the same MCP
connection. Repairable validation or execution errors are returned to a fresh
SQL-generation agent for at most two corrections. Initial translations use a
bounded, process-local LRU cache; cached SQL is still validated by MCP every time
it executes.

The MCP server remains the security boundary: it rejects writes, DDL, unsafe
SQLite operations, multiple statements, oversized results, and long-running
queries.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run complexipy . --max-complexity-allowed 15 --failed --color no
```
