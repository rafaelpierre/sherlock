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
