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

Submit a standalone analytics question directly to the Text2SQL service:

```bash
curl -X POST http://localhost:8080/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which card type has the highest fraud rate?"}'
```

The response includes the normalized SQL, tabular result, generation attempt
count, and whether the initial natural-language-to-SQL translation was served
from the process-local cache.

## Use the conversational API

`POST /v1/chat` creates a fresh ChatAgent for each request. The browser supplies
bounded recent history and explicit working state; the backend does not retain
hidden conversation state between requests.

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

For example, a custom local checkout can use:

```bash
SHERLOCK_MCP_STDIO_ARGS='["--directory","/work/mcp","run","fraud-mcp-stdio"]' \
uv run backend "How many transactions are unlabelled?"
```

Keep credentials in the environment or a secret manager; do not commit bearer
tokens in header configuration. For a public AWS endpoint, add authentication
and TLS at the MCP service or reverse-proxy layer.

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
