# Sherlock

Sherlock is a natural-language fraud analytics service. A FastAPI backend uses
Strands Agents and Amazon Bedrock to translate questions into SQLite, then sends
the SQL to a read-only MCP server for validation and execution against the
bundled fraud dataset.

## What is implemented

- `POST /v1/query` returns the generated SQL, tabular results, retry count, and
  cache status.
- Schema-aware SQL generation with bounded caching and automatic repair retries.
- A FastMCP server exposing schema, sample-value, database-info, and query tools.
- Read-only SQL enforcement, row limits, timeouts, and an in-memory database
  snapshot.
- Stdio and Streamable HTTP MCP transports, plus Docker support for the MCP
  server.
- Terraform scaffolding for Amazon Bedrock AgentCore, ECR, and IAM.

## Run locally

Requires Python 3.13+, [`uv`](https://docs.astral.sh/uv/), AWS credentials, and
access to a Bedrock model.

```bash
cd mcp && uv sync && uv run python db/prepare_database.py
cd ../backend && uv sync && uv run backend-api
```

The backend starts the MCP server over stdio by default. Query it with:

```bash
curl -X POST http://localhost:8080/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which card type has the highest fraud rate?"}'
```

See [backend/README.md](backend/README.md) and [mcp/README.md](mcp/README.md) for
configuration, remote transport, Docker, and test instructions.

## Next steps

1. Frontend
2. Terraform
  2.1. Finish implementation for the remaining AWS components
3. Observability
4. Evals
  4.1. Basic cases
  4.2. More complex scenarios (e.g. `what are some insights about fraudulent transactions?`)
