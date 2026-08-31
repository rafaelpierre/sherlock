# Sherlock Repository Guidance

This file is the operating guide for contributors and coding agents working in
this repository. Follow it for every change unless a more specific `AGENTS.md`
exists below the files being changed.

## Project Summary

Sherlock is a decision-support application for fraud success managers. It turns
natural-language questions into analytics, helps create candidate fraud rules,
validates them, and replays them against historical transactions. Candidate
rules are hypotheses for investigation; they are not production fraud
decisions.

The current system has five main areas:

- `backend/`: a Python 3.13 FastAPI application using Strands Agents and Amazon
  Bedrock. It owns the stateless chat orchestration, Text2SQL generation/repair,
  candidate-rule validation and generation/refinement, historical backtesting,
  and shared evaluation-runner code.
- `mcp/`: a Python 3.13 FastMCP server that owns schema inspection and the
  read-only SQLite query boundary. It validates SQL, applies limits and
  timeouts, and executes against an in-memory snapshot of the bundled dataset.
- `frontend/`: a Vite, React, and TypeScript investigation workspace. It owns
  the browser's bounded conversation history and working state, validates API
  contracts with strict Zod schemas, and renders typed artifacts and public
  stream events.
- `evals/`: versioned evaluation fixtures, schemas, and the Text2SQL HTTP
  evaluation CLI. The shared deterministic evaluation runner currently lives in
  `backend/`; keep its cross-package contract explicit when changing either
  area. Result-correctness oracles compare normalized result semantics, not
  generated SQL text; retain explicit cohort/null expectations in those cases.
- `terraform/`: a partial AWS scaffold that currently provisions AgentCore,
  ECR, and IAM resources. It is not the final deployment architecture: follow
  [#61](https://github.com/rafaelpierre/sherlock/issues/61) for the unresolved
  frontend, backend, MCP, and observability runtime design.

The dated files in `specs/` describe the product direction and original
backlog. GitHub Issues are the current source of truth for delivery status,
priority, acceptance criteria, and follow-up work.

## Architecture and Product Invariants

- The FastAPI backend remains stateless between HTTP requests. The browser owns
  bounded conversation history, explicit working state, and bounded user-safe
  activity summaries.
- A fresh `ChatAgent` handles each `/v1/chat` request and selects exactly one
  top-level workflow: exploration, candidate-rule generation/refinement,
  backtesting, or comparison. It must not mix those state transitions in one
  turn.
- `EXPLORE` hands off once to a fresh, bounded `AnalysisAgent`. That specialist
  may perform sequential Text2SQL investigations and synthesize only from their
  completed evidence; it must use `Text2SQLService`, never a direct database
  path. Keep its query, model-turn, output, deadline, cancellation, and failure
  bounds enforced in code rather than by prompt text alone. The remaining
  cross-workflow budget hardening and cumulative SSE-artifact bound are tracked
  by [#79](https://github.com/rafaelpierre/sherlock/issues/79) and
  [#91](https://github.com/rafaelpierre/sherlock/issues/91); do not imply that
  either is already delivered. Do not introduce hidden server-side conversation,
  handoff, or plan state.
- Specialist agents and deterministic services must remain independently
  callable by their domain endpoints. Agent prose is never the authoritative
  representation of rules, metrics, or state.
- Typed backend artifacts are authoritative. In particular, each successful
  multi-step exploration result is one ordered `analysis_step` artifact that
  groups its public question, SQL, and bounded table. Update Pydantic/OpenAPI,
  frontend Zod schemas, and valid/malformed contract tests together when these
  contracts change.
- SSE exposes only product-safe `tool_call`, `tool_result`, `text_delta`,
  `complete`, and `error` events. The terminal `complete` payload is
  authoritative for artifacts, metadata, and replacement working state; never
  expose provider events, model reasoning, raw tool arguments, or unrestricted
  tool results.
- Do not persist raw transaction result sets, SQL/table artifacts, partial
  prose, or provider data in browser storage. Persist only bounded history,
  working state, and user-safe activity/failure summaries; transient streamed
  evidence may be absent after reload.

### SQL and candidate-rule safety

- Generated SQL and candidate rules must pass deterministic validation before
  execution and must execute through the MCP read-only boundary.
- Candidate rules are SQL `WHERE` predicates over the canonical
  `fraud_transactions` relation, not arbitrary statements.
- `is_fraud` is ground truth for exploration and internal metric calculation,
  but must not be usable as a candidate-rule feature.
- Preserve MCP query limits, timeouts, single-statement checks, and read-only
  enforcement. Never bypass them for convenience.

### Backtest cohort semantics

- Never treat `is_fraud IS NULL` as non-fraud.
- Confusion-matrix and fraud-value quality metrics use labelled rows only.
- Alert-volume metrics use all rows and explicitly report unlabelled flagged
  transactions.

## GitHub Issue Workflow

GitHub Issues are the live backlog. Every material change must have an issue
before implementation begins, including defects and follow-up work found during
review. A tiny typo may be folded into an already-open issue only when it is
clearly part of that issue's scope.

### Issue quality

Each issue should state:

- the problem or user outcome;
- the required scope and relevant architectural constraints;
- testable acceptance criteria;
- dependencies or ordering constraints;
- links to the originating spec, PR, or Codex comment when applicable.

Keep the issue updated if implementation discoveries materially change its
scope. Do not silently broaden a feature branch into unrelated work.

### Labels and priorities

Apply one priority label, at least one scope label, and one type label:

- Priority: `priority:P0`, `priority:P1`, `priority:P2`, or `priority:P3`.
- Scope: `scope:backend`, `scope:mcp`, `scope:frontend`,
  `scope:evaluation`, `scope:documentation`, or `scope:infrastructure`.
- Type: `type:feature`, `type:enhancement`, `type:bug`,
  `type:documentation`, or `type:infrastructure`.

Priority means:

- P0: blocks the core safe product loop; work first.
- P1: required MVP capability or high-impact correctness fix.
- P2: important follow-up, usability, resilience, or drill-down work.
- P3: polish, optional deployment, or non-blocking improvement.

Work from highest to lowest priority. Within a priority, resolve dependencies
and correctness defects before dependent features. Finish and merge one issue
before starting the next unless the issue explicitly calls for coordinated
parallel work.

### Review findings and issue closure

- A PR must contain `Closes #<issue-number>` so the squash merge closes its
  issue automatically.
- Resolve all Codex P0 and P1 findings in the current PR before merging.
- If a lower-priority finding is valid but intentionally out of scope, create a
  follow-up issue with the correct priority, scope, and type labels. Link the
  exact review comment and the originating PR.
- Apply the same process to useful comments discovered on already-merged PRs:
  create an issue instead of losing the feedback.
- Do not close an issue merely because code was written. Close it through the
  merged PR after its acceptance criteria and quality gates are satisfied.

Useful commands:

```bash
gh issue list --state open
gh issue view <issue-number> --comments
gh issue create --title "..." --body-file <file> \
  --label "priority:P1" --label "scope:backend" --label "type:feature"
```

## Development Workflow

Use one issue, one feature branch, and one PR at a time.

### 1. Start from current `main`

The worktree must be clean. Preserve user changes and stop if unrelated local
changes overlap the task.

```bash
git checkout main
git pull --ff-only
git status --short --branch
git checkout -b feature/<issue-number>-<short-slug>
```

Use the `feature/` prefix for features, fixes, documentation, CI, and
infrastructure changes so branch naming stays predictable.

### 2. Implement only the issue scope

- Read the issue, relevant specs, and nearby tests before editing.
- Prefer deterministic domain services around agent behavior.
- Add or update tests with the implementation, including failure paths and
  boundary cases.
- Preserve existing user work in a dirty worktree and avoid unrelated cleanup.
- Update documentation when behavior, setup, architecture, or commands change.

### 3. Run local quality gates

Install locked development dependencies before validating a Python package:

```bash
cd backend
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run complexipy . --max-complexity-allowed 15 --failed --color no
uv run pytest --cov=sherlock --cov-report=term-missing --cov-fail-under=80
```

For MCP changes:

```bash
cd mcp
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest --cov=fraud_mcp --cov=db --cov-report=term-missing \
  --cov-fail-under=80
```

For evaluation-package changes:

```bash
cd evals
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=sherlock_evals --cov-report=term-missing \
  --cov-fail-under=80
```

The minimum coverage floor is 80% for each affected Python package. New code
must not use unrelated well-covered modules to mask missing tests. Run focused
tests during development, then the complete affected-package command above
before opening a PR.

For Terraform changes, at minimum run:

```bash
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
```

For frontend changes:

```bash
cd frontend
npm ci
npm run lint
npm run format:check
npm run typecheck
npm run test:coverage
npm run build
```

### Browser verification

For every frontend behavior change, or any backend/API change that affects a
browser workflow, run a focused Playwright check against the locally rebuilt
Docker Compose stack before opening the PR. The check must drive a real browser
through the affected user journey and use the live `/v1` network path; do not
mock, intercept, stub, or route API/network responses for this verification.

Start the stack from the repository root with valid AWS credentials, region, and
Bedrock access:

```bash
docker compose up --build --wait
```

Use the browser at `http://localhost:3000` and exercise both the intended
success path and relevant error/retry or reload behavior. If no committed
Playwright suite exists, run a focused local Playwright script without network
mocking and record the exact scenario and result in the PR verification section.
Health checks alone do not satisfy this requirement because they do not invoke
Bedrock or the chat workflow.

For documentation-only changes, run `git diff --check` and manually verify all
commands and links. Do not invent quality commands that are not present in the
repository.

### 4. Commit, push, and open the PR

Use a focused commit message and include the issue-closing reference in the PR
body. Complete the repository PR template, especially the verification results.

```bash
git diff --check
git status --short
git add <in-scope-files>
git commit -m "<imperative summary>"
git push -u origin feature/<issue-number>-<short-slug>
gh pr create --base main --head feature/<issue-number>-<short-slug> \
  --title "<outcome>" --body-file <pr-body-file>
```

Never push feature work directly to `main`. Never combine multiple backlog
issues in one PR solely to save review time.

## CI and Codex Review

Applicable path-based CI runs on pull requests:

- `Backend CI`: locked install; Ruff lint, ty, Complexipy, and pytest with at
  least 80% coverage.
- `MCP CI`: locked install; Ruff lint, ty, and pytest with at least 80%
  coverage.
- `Frontend CI`: locked npm install; Oxlint/ESLint, Oxfmt, TypeScript,
  coverage tests, and production build.
- `Evals CI`: Ruff lint/format and pytest with at least 80% coverage.
- `Codex Review Gate`: requires a Codex review for the exact current head SHA.

The Codex review requirement applies even to documentation-only PRs. GitHub
branch protection/rulesets are unavailable while this private repository is on
its current plan, so the workflow check and this file are mandatory process
controls even when GitHub cannot technically block an override.

### Review baseline

Review every change against both its issue acceptance criteria and these
cross-cutting principles. Specific issue scope controls what belongs in the PR;
these checks identify regressions, design flaws, and follow-up work that the
issue may not have anticipated.

- **Compatibility:** trace affected API, artifact, SSE, persistence, evaluation,
  configuration, and deployment contracts across their consumers. Version or
  migrate an intentional breaking change; otherwise preserve compatibility and
  update both sides of a typed contract together.
- **Modularity and ownership:** keep orchestration, deterministic domain logic,
  model behavior, database execution, and UI rendering at their established
  boundaries. Avoid duplicated business rules, hidden state, inappropriate
  coupling, and abstractions that obscure a single clear owner.
- **Maintainability:** look for code smells such as unclear control flow,
  unbounded or duplicated logic, weak error boundaries, misleading names, and
  tests that assert implementation accidents rather than observable behavior.
- **Scalability and resilience:** assess request lifecycles, bounded work,
  concurrency, timeouts, cancellation, resource cleanup, cache ownership, and
  failure behavior. Do not rely on a frontend/proxy timeout as the only bound.
- **Security and data safety:** preserve validation and the MCP read-only query
  boundary; check authorization, secret handling, injection risks, sensitive
  data exposure, and reasoning/provider-data leakage. Candidate-rule and cohort
  safety invariants remain mandatory.
- **Operational fit:** keep documentation, local commands, CI, observability,
  and deployment assumptions consistent with the change and its runtime
  dependencies.

Record valid findings that are out of scope as labelled follow-up issues, as
described below; do not silently waive a systemic concern just because the
current feature works.

### Trigger and wait for Codex

Opening a PR may start an automatic Codex review. If one is not already in
progress, request it with a PR comment:

```bash
gh pr comment <pr-number> --body '@codex review'
```

Codex posts or updates a review summary. Wait until the summary says the review
completed for the current seven-character head SHA, or until
`chatgpt-codex-connector[bot]` submits a formal review for the full current SHA.
A friendly Codex comment alone is not sufficient if it refers to an older
commit.

Use these commands to monitor the PR:

```bash
gh pr view <pr-number> --json headRefOid,mergeStateStatus,statusCheckRollup,url
gh pr view <pr-number> --comments
gh pr checks <pr-number> --watch --interval 10
```

The gate can finish before Codex and fail with “No Codex review exists for the
current PR head.” Once Codex has completed, rerun the failed gate in GitHub or
identify and rerun it with:

```bash
gh run list --workflow codex-review.yml --branch \
  feature/<issue-number>-<short-slug>
gh run rerun <run-id>
gh run watch <run-id> --exit-status
```

The gate recognizes the exact API login
`chatgpt-codex-connector[bot]`; GitHub's UI may display a shorter friendly name.

### Respond to findings

- Review every inline finding and the summary; do not rely only on the gate's
  pass/fail result.
- Fix every P0 and P1 finding, run the full applicable local checks, commit, and
  push.
- Reply to each addressed review thread with the fixing commit and concise
  verification evidence, then explicitly mark that GitHub thread resolved. A
  new Codex review does not resolve older conversations automatically. Never
  resolve a thread while its finding is still outstanding.
- Any push changes the PR head SHA and invalidates the earlier review. Request a
  fresh `@codex review`, wait for completion on the new head, and rerun the gate
  if needed.
- Track valid deferred findings as labelled GitHub issues before merging.

Do not merge while any applicable check is pending or failing, while a Codex
review is still running, or while required findings remain unresolved.

## Merge and Synchronize

When all CI checks pass and Codex has reviewed the current head, squash-merge
and delete the remote feature branch:

```bash
gh pr checks <pr-number>
gh pr merge <pr-number> --squash --delete-branch
git checkout main
git pull --ff-only
git status --short --branch
```

Confirm that:

- the PR is merged and its issue is closed;
- local `main` matches `origin/main`;
- the worktree is clean;
- the feature branch is deleted locally and remotely.

Only then select the next issue and create its branch from the updated `main`.
