# Sherlock

Sherlock is a decision-support workspace for fraud success managers. It turns
natural-language questions into analysis, helps an investigator draft candidate
fraud rules, and replays those hypotheses against historical transactions before
any production decision is made.

> [!IMPORTANT]
> Sherlock does not deploy rules or make live fraud decisions. Generated rules
> are candidate investigation hypotheses. A human must review their logic,
> historical trade-offs, operational impact, and suitability for production.

## The problem and product hypothesis

Fraud investigations often split one feedback loop across SQL tools, notebooks,
rule editors, and manual metric calculations. The analyst must move repeatedly
between exploring a pattern, expressing it as a rule, measuring its alert cost
and fraud coverage, and refining it.

Sherlock tests a focused hypothesis: a conversational workspace can shorten that
loop while deterministic services retain control of query execution, rule
validation, and metric calculation.

```text
Ask -> discover -> form a hypothesis -> draft a candidate rule
    -> replay it historically -> understand trade-offs -> refine
```

The current prototype supports:

- natural-language exploration of the bundled fraud dataset;
- schema-grounded, read-only Text2SQL with bounded repair attempts;
- generation and refinement of candidate SQL `WHERE` predicates;
- historical replay with alert-volume, confusion-matrix, and fraud-value metrics;
- comparison of the current and previous candidate rule;
- a React workspace with streamed progress, typed artifacts, and bounded local
  conversation state; and
- independently callable query, rule-generation, refinement, backtest, and
  comparison endpoints.

## Demo workflow

Start the full stack, open <http://localhost:3000>, and try this investigation:

1. `Which card types have the highest fraud rate?`
2. `Create a candidate rule for debit transactions above $1,000.`
3. `Backtest it.`
4. `Raise the threshold to $1,500.`
5. `Compare that with the previous rule.`

Sherlock displays prose separately from authoritative artifacts: the generated
SQL and table, validated candidate rule, historical replay metrics, and rule
comparison. **New investigation** clears the browser-owned conversation and
working state.

## Architecture

```text
React browser workspace
  |  POST /v1/chat (bounded history + explicit working state)
  v
FastAPI ChatAgent -- EXPLORE --> bounded AnalysisAgent
                                  | sequential analytical questions
                                  v
                              Text2SQL service --> read-only MCP --> SQLite
  |
  +-- candidate rules --> generation/refinement --> deterministic validation
  |
  +-- replay/compare --> deterministic backtest and comparison --> read-only MCP
```

- [`frontend/`](frontend/) is a Vite/React/TypeScript client. It keeps at most
  20 messages, explicit working state, and bounded user-safe activity summaries
  in `localStorage`; raw query result tables are not persisted.
- [`backend/`](backend/) is a Python 3.13 FastAPI application built with Strands
  Agents and Amazon Bedrock. A fresh `ChatAgent` handles each `/v1/chat` request,
  and a fresh bounded `AnalysisAgent` handles each `EXPLORE` handoff, so the
  backend retains no hidden conversational or investigation state.
- [`mcp/`](mcp/) is a FastMCP service and the only database execution boundary.
  It exposes schema inspection, bounded sample values, database metadata, and
  read-only query execution over an in-memory snapshot of the bundled SQLite
  dataset.
- [`evals/`](evals/) contains versioned evaluation fixtures, the report schema,
  and reproducibility guidance.
- [`terraform/`](terraform/) contains optional AWS AgentCore, ECR, and IAM
  scaffolding; it is not required for local use.

The conversational response contains typed artifacts and authoritative
replacement working state, so the frontend never has to recover rules or
metrics by parsing agent prose. Domain services remain callable through their
own `/v1/query` and `/v1/rules/*` endpoints.

## Safety and reliability boundaries

Sherlock uses the model for language understanding and candidate generation,
then applies deterministic controls before data access or historical scoring:

- SQL generators can inspect schema and bounded sample values but cannot execute
  queries. The Text2SQL service executes their output through MCP and permits at
  most two repair attempts.
- An analytical handoff can adapt sequential questions to earlier results, but
  is capped at five attempted queries, seven model turns, and 240 seconds. Every
  successful step is returned as a grouped question/SQL/table artifact.
- MCP accepts one SQLite `SELECT` or `WITH` statement, rejects writes, DDL,
  `PRAGMA`, `ATTACH`, extension loading, and multiple statements, applies row
  limits and a best-effort timeout, and uses query-only connections.
- Candidate rules must be a single predicate over the canonical
  `fraud_transactions` relation. They cannot contain statements, comments,
  subqueries, unknown columns, or the outcome-only `is_fraud` label.
- Backtests validate a rule again before execution. Precision, recall,
  false-positive rate, and fraud-value quality use labelled rows only;
  `is_fraud IS NULL` is never treated as non-fraud. Alert volume uses all rows
  and reports unlabelled flagged transactions separately.
- The browser sends bounded history and structured referents on every request.
  Missing or invalid state produces a structured error instead of asking the
  model to invent it.
- Streamed model/provider events are translated to a small public SSE contract.
  Raw reasoning, tool arguments, provider payloads, and raw tool results are not
  exposed to the browser.

These controls reduce risk; they do not establish production readiness or prove
that generated analysis and rules are correct.

## Evaluation

The committed baseline as of **2026-08-30** is:

| Suite | Mode / model | Dataset context | Result | Repairs |
|---|---|---|---:|---:|
| `runner-smoke` | fixture / `deterministic-fixture-v1` | bundled SQLite blob `2831beeb4444e6f32d6226dc0d872a4e9baf3bde` | 1/1 passed (100%) | 0 |

The [machine-readable report](evals/results/runner-smoke-2026-08-30.json)
records the run date, runner configuration, dataset revision, per-case output,
latency, and repair metadata. After the one-time dependency installation, the
evaluation itself runs without AWS credentials or network access:

```bash
cd backend
uv sync --locked --dev
uv run sherlock-eval \
  --suite runner-smoke \
  --output ../eval-results/runner-smoke.json
```

This score verifies fixture loading, recursive comparison, aggregation, and
report generation only. The fixture does not call Bedrock, MCP, or the bundled
database, even though the report records the repository dataset revision. It is
not a Text2SQL correctness score or a candidate-rule quality score. No committed
live-model score currently exists. Semantic golden suites and reviewed live
Bedrock baselines remain next steps; see the [evaluation guide](evals/README.md)
for live-run requirements and report semantics.

The bundled snapshot contains 1,159,966 transactions from 2019-01-01 through
2019-10-31. Of these, 777,339 have labels, 1,360 are labelled fraud, and 382,627
are unlabelled. Those coverage facts are important when interpreting any future
quality or backtest metric.

## Setup

### Full stack with Docker Compose

Requirements:

- Docker with Compose v2;
- AWS credentials and an AWS region; and
- access to an Amazon Bedrock model supported by Strands.

From the repository root:

```bash
docker compose up --build --wait
```

Compose mounts `~/.aws` read-only by default and uses the standard AWS
credential chain. To select another profile or credentials directory:

```bash
AWS_PROFILE=sherlock \
AWS_CONFIG_DIR=/path/to/.aws \
AWS_REGION=eu-west-2 \
docker compose up --build --wait
```

Open <http://localhost:3000>. Check each layer with:

```bash
curl --fail http://localhost:3000/health
curl --fail http://localhost:3000/v1/health
curl --fail http://localhost:8080/v1/health
curl --fail http://localhost:8000/health
```

Health checks prove that the processes are ready; they do not call Bedrock.
Analytical and chat requests still require valid credentials, a region, and
model access. Stop the stack with `docker compose down`.

### AWS PoC deployment

The optional AWS PoC deploys the frontend and backend as separate ECS Express
Mode services in `eu-west-2`. Express Mode supplies each service's managed HTTPS
endpoint, TLS termination, logging, health checks, and one-task deployment
defaults. The browser service proxies `/v1` to the backend service. The MCP
server runs as an ARM64 MCP-protocol Amazon Bedrock AgentCore Runtime; the
backend task uses its ECS task identity to invoke it, so no AWS access keys are
stored in an image or workflow secret.

Bootstrap the account once from a workstation with AWS administrator access:

```bash
terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap apply \
  -var='project_name=sherlock' \
  -var='github_owner=rafaelpierre' \
  -var='github_repo=sherlock'
```

This creates the `sherlock-frontend`, `sherlock-backend`, and `sherlock-mcp` ECR
repositories, the GitHub OIDC role, and the S3 state bucket. Each service has
its own publishing workflow: **Publish frontend image**, **Publish backend
image**, and **Publish MCP image**. A merge to `main` runs only the workflow
whose service build context changed. It pushes that service's immutable,
multi-architecture image under the merge commit SHA. Pull requests build-test
both target architectures for only the affected service and never write to ECR.
Each successful publication uploads a 90-day `image-release.json` artifact with
the service name, source commit, image tag, and manifest digest. The deployment
workflow uses these artifacts to select immutable releases; their expiry does
not affect images already deployed to ECS or AgentCore.

To deploy the normal release set, trigger **Deploy AWS PoC** manually and leave
the three `*_image_tag` override inputs empty. The workflow retrieves the most
recent unexpired frontend, backend, and MCP release artifacts and deploys the
image tags recorded in their metadata. This works even though each service is
published independently on the merge that changes it.

For a deliberate mixed-version deployment or rollback, set one or more explicit
`*_image_tag` overrides. An override must be a lowercase 40-character commit
SHA from the corresponding service publication; services without an override
continue to resolve from their latest release artifact. Before Terraform runs,
the workflow validates release metadata for artifact-resolved images and
verifies every selected tag exists in its corresponding ECR repository as a
multi-architecture manifest. Missing or malformed artifacts, missing images, and
architecture-specific tags fail without substituting another image. The workflow
logs the selected release and final tag/digest for each service, then applies
Terraform and calls the public frontend `/health` and proxied `/v1/health`
endpoints. It never rebuilds, pushes, or retags an image. Its state bucket and
account are intentionally fixed to this PoC account (`041391475835`) and region
(`eu-west-2`).

The initial deployment usually takes several minutes because ECS Express Mode
creates its managed ingress resources and AgentCore creates a runtime revision.
Use the workflow's Terraform output `frontend_url` to open the application.
If a Terraform apply fails after creating resources, correct the configuration
and rerun **Deploy AWS PoC**. Terraform records the resources it created in the
remote state, so do not manually remove them or their state entries before the
retry unless the failure specifically requires AWS cleanup.

### Run the application directly

Requirements are Python 3.13+, [`uv`](https://docs.astral.sh/uv/), Node.js
22.12+, AWS credentials, an AWS region, and Bedrock model access.

Start the backend in one terminal. Its default stdio transport launches the MCP
server from the sibling project, so no separate MCP process is required:

```bash
cd backend
uv sync --locked --dev
uv run backend-api
```

Start the frontend in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/v1` to the backend at
<http://localhost:8080>.

To call the standalone Text2SQL endpoint instead of the browser:

```bash
curl -X POST http://localhost:8080/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which card type has the highest fraud rate?"}'
```

The bundled database is already prepared. Refresh and validate its canonical
view after changing the source database with:

```bash
cd mcp
uv sync --locked --dev
uv run python db/prepare_database.py
```

Service-specific configuration and checks are documented in the
[backend](backend/README.md), [MCP](mcp/README.md), and
[frontend](frontend/README.md) guides.

## Limitations

- The only committed evaluation is a one-case deterministic runner smoke test;
  model accuracy, semantic rule quality, unsafe-query rate, and end-to-end
  reliability are not yet benchmarked.
- The dataset is a fixed historical SQLite snapshot with substantial unlabelled
  coverage. Backtests are retrospective associations, not estimates of future
  production performance or causal impact.
- Backtests return aggregate metrics, not transaction-level false-positive,
  true-positive, or false-negative drill-downs.
- The model can still misunderstand a question or propose a poor but syntactically
  valid rule. Human review remains mandatory.
- Browser state is local to one browser profile and is not shared, durable, or a
  system of record. The backend is stateless between requests.
- Local MCP HTTP has no authentication or TLS. Bindings are loopback-only in
  Compose, but standalone deployments must use a trusted private network or an
  authenticated TLS proxy.
- The AWS deployment is a one-account PoC. It intentionally has no custom
  domain, environment promotion, private network topology, application
  authentication, or production telemetry/retention policy.

## Next steps

1. Add 20-30 semantic Text2SQL golden cases scored by result correctness.
2. Add candidate-rule cases that validate meaning and, where practical, matched
   transaction IDs; publish a reviewed live-model baseline with full context.
3. Add transaction-level backtest inspection for flagged rows and confusion-
   matrix cohorts.
4. Improve request, model, MCP, cache, and backtest observability.
5. Complete and validate the optional AWS deployment path.

Delivery status and acceptance criteria live in
[GitHub Issues](https://github.com/rafaelpierre/sherlock/issues); dated files in
`specs/` are product-direction references rather than the active backlog.
