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
- Stdio and Streamable HTTP MCP transports, plus Docker support for the full
  application stack.
- A stateless React investigation workspace for conversational analysis,
  candidate-rule iteration, backtesting, and comparison.
- Terraform scaffolding for Amazon Bedrock AgentCore, ECR, and IAM.

## Run the full stack with Docker Compose

Requires Docker with Compose v2, AWS credentials, and access to a Bedrock model.
The backend uses the standard AWS credential chain. By default, Compose mounts
`~/.aws` read-only and selects the `default` profile:

```bash
docker compose up --build --wait
```

Open <http://localhost:3000>. The individual health endpoints are available at
<http://localhost:3000/health>, <http://localhost:8080/v1/health>, and
<http://localhost:8000/health>. Browser requests under `/v1` are proxied by the
frontend container to the backend; the backend connects to MCP over the private
Compose network.

Verify the running stack, including the browser-facing backend proxy, with:

```bash
curl --fail http://localhost:3000/
curl --fail http://localhost:3000/health
curl --fail http://localhost:3000/v1/health
curl --fail http://localhost:8080/v1/health
curl --fail http://localhost:8000/health
```

To use another profile or credentials directory:

```bash
AWS_PROFILE=sherlock AWS_CONFIG_DIR=/path/to/.aws docker compose up --build --wait
```

Environment credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and an
optional `AWS_SESSION_TOKEN`) and `AWS_REGION`/`AWS_DEFAULT_REGION` are passed
through when set. Host ports can be changed with `FRONTEND_PORT`,
`BACKEND_PORT`, and `MCP_PORT` without changing container-to-container URLs.

Useful lifecycle commands:

```bash
docker compose ps
docker compose logs --follow
docker compose down
```

If startup does not become healthy, inspect `docker compose logs mcp backend`
first. A missing AWS profile does not prevent the containers from starting, but
Bedrock-backed requests require valid credentials, a region, and model access.

## Run services directly

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

See [backend/README.md](backend/README.md), [mcp/README.md](mcp/README.md), and
[frontend/README.md](frontend/README.md) for service-specific configuration,
Docker, and test instructions.

## Next steps

1. Frontend
2. Terraform
  2.1. Finish implementation for the remaining AWS components
3. Observability
4. Evals
  4.1. Basic cases
  4.2. More complex scenarios (e.g. `what are some insights about fraudulent transactions?`)
