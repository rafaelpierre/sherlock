# Fraud Analytics MCP server

This FastMCP service is Sherlock’s agentic data-capability boundary. It exposes
only safe analytical tools over the supplied SQLite dataset; neither a Strands
agent nor the FastAPI backend receives a direct SQLite connection. The [root
README](../README.md) explains how this server fits the orchestrator, specialist
handoff, and deployed AgentCore architecture.

At startup it copies the prepared database into a shared in-memory SQLite
snapshot. Each request obtains a query-only connection to that snapshot, so
tool calls avoid filesystem I/O and source changes take effect only after a
restart.

## Setup and local use

Requirements: Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --dev
uv run python db/prepare_database.py
uv run fraud-mcp
```

Default addresses:

```text
MCP endpoint: http://localhost:8000/mcp
Health check: http://localhost:8000/health
```

The server binds to `0.0.0.0` by default, but clients must use `localhost` or a
reachable hostname, not `0.0.0.0`. The HTTP transport is Streamable HTTP. For a
client that owns the subprocess, use the separate stdio entry point:

```bash
uv run fraud-mcp-stdio
```

Protocol data is stdout; logs remain stderr.

## The available tools

| Tool | Purpose | Bound |
|---|---|---|
| `get_schema` | Return relations, columns, types, and the recommended canonical relation. | Read-only metadata. |
| `get_sample_values` | Return distinct values for one validated relation/column. | Maximum 50 values. |
| `run_query` | Execute a validated analytical statement. | `SELECT`/`WITH` only, row ceiling and timeout. |
| `get_database_info` | Return database metadata. | Read-only metadata. |

The canonical `fraud_transactions` view is one row per transaction and flattens
the provided `transactions`, `cards`, `users`, `mcc_codes`, and `fraud_labels`
tables. It includes documented derived fields such as dollar-denominated values,
time features, and amount-to-credit-limit ratio. Preparation validates the
view’s grain, columns, conversions, and label coverage; missing fraud labels
remain `NULL`.

## Why this is an agentic safety boundary

Tool calling is not trust. The backend limits which tools a caller can discover
for a particular workflow; this server then independently validates request
parameters and SQL. `run_query` accepts one `SELECT` or `WITH` statement,
rejects writes, DDL, `PRAGMA`, `ATTACH`, extension loading, and multiple
statements, and uses `PRAGMA query_only=ON` as defence in depth. Result rows are
bounded and execution has a best-effort SQLite deadline. Structured errors for
unknown columns/relations and timeout are returned to the backend’s controlled
Text2SQL repair path, not silently masked.

This is intentionally not a general-purpose data agent: there is no shell,
filesystem API, write capability, or unbounded result export. Candidate-rule
validation in the backend is an additional guard; it forbids `is_fraud` as a
rule feature and validates a predicate again before backtesting.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_PATH` | `db/data/data.db` | Prepared SQLite path |
| `MAX_QUERY_ROWS` | `100` | Normal returned-row cap |
| `HARD_MAX_QUERY_ROWS` | `1000` | Absolute configured row ceiling |
| `QUERY_TIMEOUT_SECONDS` | `10` | SQLite deadline |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `MCP_HOST` | `0.0.0.0` | HTTP bind host |
| `MCP_PORT` | `8000` | HTTP port |
| `MCP_PATH` | `/mcp` | Streamable HTTP path |

To prepare another compatible dataset:

```bash
uv run python db/prepare_database.py --database /path/to/fraud.sqlite
```

## Deployment and security

In Docker Compose, MCP is loopback-published to the host and privately reached
by the backend as `http://mcp:8000/mcp`. In the delivered AWS architecture, the
same MCP protocol service runs in Amazon Bedrock AgentCore Runtime; the backend
uses its ECS task role and SigV4 to invoke it. No static AWS credential is
stored in the image.

Standalone HTTP mode has no built-in authentication or TLS. Deploy it only on a
trusted private network, or place it behind an authenticated TLS proxy or a
supported FastMCP authentication provider.

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest --cov=fraud_mcp --cov=db --cov-report=term-missing --cov-fail-under=80
```
