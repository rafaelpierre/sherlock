# Sherlock frontend

Sherlock's Vite, React, and TypeScript client is a stateless investigation
workspace. It sends browser-owned bounded conversation history and explicit
working state to `POST /v1/chat`, then renders assistant prose and typed
artifacts without parsing prose for authoritative data. Persisted transcript
entries contain prose only; raw table artifacts are never written to browser
storage.

```bash
npm ci
npm run dev
```

Vite proxies `/v1` to `http://localhost:8080`, so start the backend locally on
that port.

## Docker

The production-style image builds the static application with locked npm
dependencies, serves it with Nginx, and proxies `/v1` to the Compose backend:

```bash
docker build -t sherlock-frontend .
```

The proxy target uses the Compose service name, so run the image through the
root `docker-compose.yaml` rather than by itself:

```bash
docker compose up --build --wait
```

Open <http://localhost:3000>. Client-side routes fall back to `index.html`, and
`GET /health` is used for container readiness. Proxied API requests allow up to
five minutes for multi-step analytical turns to return a response.

Successful chat responses are validated with the Zod schemas in
`src/types.ts` before application state is updated. Those schemas are the
frontend source for both runtime validation and inferred TypeScript types; they
mirror the Pydantic response models in `backend/src/sherlock/api/chat_models.py`
and `backend/src/sherlock/api/artifacts.py`, which remain the API/OpenAPI source
of truth. Contract changes must update the backend models, the co-located Zod
schemas, and their valid/malformed response tests together. Unknown artifact
types are rejected at this boundary and use the normal recoverable chat-error
path.

Quality checks:

```bash
npm run lint
npm run format:check
npm run typecheck
npm run test:coverage
npm run build
```

Run `npm run format` to apply Oxfmt changes locally.
