# Fraud Analytics MCP Server

A read-only Model Context Protocol server for analytical access to the local
SQLite fraud dataset. It exposes a flattened `fraud_transactions` view designed
for Text2SQL and serves MCP over Streamable HTTP for remote clients.

At startup, the server copies the prepared SQLite database into a shared
in-memory database. Tool calls use connections to that snapshot, avoiding
filesystem I/O; changes to the source file take effect after a server restart.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Setup

From this directory:

```bash
uv sync
```

Prepare or refresh the canonical analytical view:

```bash
uv run python db/prepare_database.py
```

To prepare a different compatible SQLite database:

```bash
uv run python db/prepare_database.py --database /path/to/fraud.sqlite
```

Preparation is idempotent. It validates the canonical columns, one-row-per-
transaction grain, unique transaction IDs, conversions, derived values, and
fraud-label coverage. Missing labels remain `NULL` rather than becoming false.

## Start the server

```bash
uv run fraud-mcp
```

The defaults expose:

```text
MCP endpoint: http://0.0.0.0:8000/mcp
Health check: http://0.0.0.0:8000/health
```

Use `localhost` or the machine's reachable hostname instead of `0.0.0.0` in a
client URL.

Example remote client configuration:

```json
{
  "mcpServers": {
    "fraud-analytics": {
      "url": "http://fraud-mcp.internal:8000/mcp"
    }
  }
}
```

The endpoint uses Streamable HTTP, not stdio or the legacy HTTP+SSE transport.

For a local MCP client that owns the server subprocess, use the dedicated stdio
entry point instead:

```bash
uv run fraud-mcp-stdio
```

Protocol messages are written to stdout and server logs remain on stderr.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_PATH` | `db/data/data.db` | Prepared SQLite database |
| `MAX_QUERY_ROWS` | `100` | Maximum rows normally returned |
| `HARD_MAX_QUERY_ROWS` | `1000` | Absolute configured row ceiling |
| `QUERY_TIMEOUT_SECONDS` | `10` | Best-effort SQLite execution deadline |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `MCP_HOST` | `0.0.0.0` | HTTP bind address |
| `MCP_PORT` | `8000` | HTTP listen port |
| `MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |

For example:

```bash
DATABASE_PATH=/data/fraud.sqlite \
MCP_PORT=9000 \
QUERY_TIMEOUT_SECONDS=5 \
uv run fraud-mcp
```

## MCP tools

### `get_schema`

Returns tables, views, columns, primary keys, and foreign keys. The response
recommends `fraud_transactions`, whose grain is one row per transaction.

### `get_sample_values`

Returns distinct values for a validated relation and column:

```json
{
  "relation": "fraud_transactions",
  "column": "transaction_type",
  "limit": 20
}
```

The maximum limit is 50.

### `run_query`

Validates and runs one read-only analytical query:

```json
{
  "sql": "SELECT card_type, AVG(is_fraud) AS fraud_rate FROM fraud_transactions GROUP BY card_type ORDER BY fraud_rate DESC"
}
```

`SELECT`, `WITH`, joins, aggregates, analytical subqueries, and window functions
are supported. DDL, DML, `PRAGMA`, `ATTACH`, multiple statements, and extension
loading are rejected. The source file is opened with `mode=ro`, and runtime
in-memory connections use `PRAGMA query_only=ON` as defense in depth.

### `get_database_info`

Returns transaction and label counts, date coverage, the canonical relation,
and read-only status.

## Analytical conventions

- Monetary fields ending in `_usd_cents` preserve source integer values.
- Corresponding `_usd` fields are expressed in dollars.
- `is_fraud` is `1` for confirmed fraud, `0` for confirmed non-fraud, and
  `NULL` for unlabelled transactions.
- `transaction_day_of_week` follows SQLite: Sunday is `0`, Monday is `1`, and
  Saturday is `6`.
- Source anomalies are preserved. Preparation reports negative card or user age
  rows rather than silently changing them.

## Tests and checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Tests use temporary SQLite databases. They cover database preparation, SQL
validation and execution, result limits, timeout interruption, structured
errors, read-only behavior, and initialization/tool calls through Streamable
HTTP.

## Docker

Build and run:

```bash
docker build -t fraud-analytics-mcp .
docker run --rm -p 8000:8000 fraud-analytics-mcp
```

The image prepares the bundled database during the build and the runtime loads
it into a read-only in-memory snapshot.

## Network security

The application does not enable authentication or TLS by default. Binding to
`0.0.0.0` makes it reachable wherever the host firewall permits. Deploy it only
on a trusted private network, or place it behind an authenticating TLS reverse
proxy or a supported FastMCP authentication provider. Do not expose an
unauthenticated instance to the public internet.

Ensure a reverse proxy permits streaming HTTP responses and gives analytical
requests at least `QUERY_TIMEOUT_SECONDS` plus normal network overhead.
