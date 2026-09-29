# Sherlock frontend

The frontend is a Vite, React, and TypeScript investigation workspace for
Sherlock's agentic AI demo. Its job is to make a Fraud Manager’s evidence,
candidate rules, and historical metrics legible without treating assistant prose
as authoritative data. The backend remains stateless; this client owns bounded
conversation context and explicit working state. See the [root README](../README.md)
for the agentic and cloud architecture.

## Run locally

Requirements: Node.js 22.12+ and a backend listening on port 8080.

```bash
npm ci
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/v1` to `http://localhost:8080`.
For the production-style, fully integrated stack, use `docker compose up --build --wait`
from the repository root and open <http://localhost:3000>.

## Browser-state boundary

The client stores a versioned `sherlock.conversation` payload in `localStorage`:

- a conversation ID;
- at most 20 compact conversation messages;
- the authoritative working state (for example a candidate rule, last SQL, and
  backtest context); and
- at most 50 user-safe activity summaries per assistant turn.

It never persists raw result tables, transaction rows, provider events, raw tool
arguments/results, internal tool names, or partial streamed prose. **New
investigation** clears runtime and persisted state. Compatible older payloads are
migrated on reload; invalid payloads are discarded safely.

This boundary is deliberate: it enables a follow-up such as “Backtest it” while
keeping the server stateless and preventing the browser from becoming an
unbounded evidence store.

## API and rendering contract

The client sends `POST /v1/chat` with the bounded history and working state.
It prefers Server-Sent Events and renders progress from only these event types:

| Event         | UI use                                                                                        |
| ------------- | --------------------------------------------------------------------------------------------- |
| `text_delta`  | Display an introduction or result-summary segment.                                            |
| `tool_call`   | Start a user-safe activity indicator.                                                         |
| `tool_result` | Complete that activity with success/failure.                                                  |
| `complete`    | Validate and commit assistant text, typed artifacts, metadata, and replacement working state. |
| `error`       | Present a recoverable, user-safe failure.                                                     |

Zod schemas in `src/types.ts` validate all completed payloads and public stream
events before state changes. They mirror backend Pydantic models in
`backend/src/sherlock/api/`; contract changes must update both sides and their
valid/malformed-response tests. The terminal `complete` payload is authoritative:
the UI does not parse generated prose to recover a rule, metric, or SQL result.

## Deployment

The container builds the static app with locked dependencies and Nginx serves
it. Nginx proxies `/v1` to `BACKEND_URL`; Compose uses `http://backend:8080`,
and the deployed ECS Express frontend receives the ECS Express backend endpoint.
`GET /health` supports readiness. API proxy timeout allows a five-minute
bounded analysis turn.

```bash
docker build -t sherlock-frontend .
```

Run the image with Compose rather than standalone, because it needs its backend
proxy target.

## Checks

```bash
npm run lint
npm run format:check
npm run typecheck
npm run test:coverage
npm run build
```

Use `npm run format` to apply Oxfmt formatting.
