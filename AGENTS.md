# Sherlock Repository Guidance

## Pull Request Delivery Rules

- Implement each backlog issue on its own `feature/<issue-number>-<slug>` branch.
- Open a pull request into `main`, wait for all applicable CI checks, and request
  a Codex review with `@codex review` unless an automatic review is already in
  progress.
- Do not merge until `chatgpt-codex-connector[bot]` has submitted a review for
  the pull request's current head commit.
- Resolve every Codex P0 or P1 finding before merging. After pushing a fix,
  request and wait for a fresh review because reviews of older commits do not
  satisfy the gate.
- Squash-merge the pull request and delete its feature branch. Synchronize
  `main` before creating the next feature branch.
- GitHub branch protection is unavailable while this repository is private on
  its current plan. Treat the `Codex Review Gate` check and these instructions
  as mandatory even though GitHub cannot technically prevent an override.

## Code Review Rules

### SQL and candidate-rule safety

- Flag any path that executes generated SQL or candidate rules without passing
  through deterministic validation and the MCP read-only execution boundary.
  The safe path parses and validates first, then executes through MCP.

### Backtest cohort semantics

- Flag quality metrics that treat `is_fraud IS NULL` as non-fraud. Confusion and
  fraud-value metrics use labelled rows; alert volume uses all rows and reports
  unlabelled flagged transactions explicitly.

### Stateless conversation ownership

- Flag hidden server-side conversation state or persistence of raw transaction
  result sets. The client supplies bounded history and explicit working state;
  only small summaries may be persisted in the browser.
