# Sherlock Text2SQL Agent

A small [Strands Agents](https://strandsagents.com/) application that answers
natural-language fraud analytics questions through the MCP server in `../MCP`.
The backend never opens SQLite directly: Strands discovers the MCP tools and the
MCP server validates and executes the generated read-only SQL.

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
  "args": ["--directory", "../MCP", "run", "fraud-mcp-stdio"]
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
SHERLOCK_MCP_STDIO_ARGS='["--directory","/work/MCP","run","fraud-mcp-stdio"]' \
uv run backend "How many transactions are unlabelled?"
```

Keep credentials in the environment or a secret manager; do not commit bearer
tokens in header configuration. For a public AWS endpoint, add authentication
and TLS at the MCP service or reverse-proxy layer.

## How Text2SQL works

The system prompt directs the agent to inspect `get_schema`, optionally inspect
bounded values with `get_sample_values`, generate one SQLite `SELECT`/`WITH`
statement, and execute it with `run_query`. The MCP server remains the security
boundary: it rejects writes, DDL, unsafe SQLite operations, multiple statements,
unknown schema references, oversized results, and long-running queries.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
