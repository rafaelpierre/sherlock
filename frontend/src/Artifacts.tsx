import { useState } from "react";
import type { Artifact, BacktestMetrics, BacktestResult } from "./types";

const integer = new Intl.NumberFormat("en-US");
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function Metric({
  label,
  value,
  change,
  changeLabel,
}: {
  label: string;
  value: string;
  change?: number | null;
  changeLabel?: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {change !== undefined && change !== null && (
        <small className={change >= 0 ? "positive" : "negative"}>
          {changeLabel ?? `${change >= 0 ? "+" : ""}${change.toFixed(1)}`}
        </small>
      )}
    </div>
  );
}

function Metrics({ metrics }: { metrics: BacktestMetrics }) {
  return (
    <div className="metrics-grid">
      <Metric label="Precision" value={percent(metrics.precision)} />
      <Metric label="Recall" value={percent(metrics.recall)} />
      <Metric label="Flagged" value={integer.format(metrics.transactions_flagged)} />
      <Metric label="Fraud caught" value={integer.format(metrics.fraud_caught)} />
      <Metric label="Value captured" value={money.format(metrics.fraud_value_captured_usd)} />
      <Metric label="Alerts / day" value={metrics.alerts_per_day?.toFixed(1) ?? "—"} />
    </div>
  );
}

function RuleCode({ children }: { children: string }) {
  return <code className="rule-code">{children}</code>;
}

function BacktestCard({
  result,
  title = "Historical replay",
}: {
  result: BacktestResult;
  title?: string;
}) {
  return (
    <section className="artifact-card">
      <div className="artifact-heading">
        <span className="artifact-kicker">Validated simulation</span>
        <h3>{title}</h3>
      </div>
      <RuleCode>{result.rule}</RuleCode>
      <Metrics metrics={result.metrics} />
      {result.metrics.unlabelled_flagged > 0 && (
        <p className="artifact-note">
          Includes {integer.format(result.metrics.unlabelled_flagged)} unlabelled flagged
          transactions. Quality metrics use labelled data only.
        </p>
      )}
    </section>
  );
}

function TableArtifact({ artifact }: { artifact: Extract<Artifact, { type: "table" }> }) {
  const possibleNumericColumn =
    artifact.rows.length > 0
      ? artifact.rows[0].findIndex((cell, index) => index > 0 && typeof cell === "number")
      : -1;
  const numericColumn =
    possibleNumericColumn > 0 &&
    artifact.rows.every((row) => {
      const value = row[possibleNumericColumn];
      return typeof value === "number" && Number.isFinite(value) && value >= 0;
    })
      ? possibleNumericColumn
      : -1;
  const [view, setView] = useState<"chart" | "result">(numericColumn > 0 ? "chart" : "result");
  const chartRows = numericColumn > 0 ? artifact.rows.slice(0, 20) : [];
  const max = Math.max(0, ...chartRows.map((row) => Number(row[numericColumn])));
  return (
    <section className="data-card">
      <div className="artifact-tabs" role="tablist" aria-label="Result display">
        {numericColumn > 0 && (
          <button
            type="button"
            role="tab"
            aria-selected={view === "chart"}
            onClick={() => setView("chart")}
          >
            ▥ Chart
          </button>
        )}
        <button
          type="button"
          role="tab"
          aria-selected={view === "result"}
          onClick={() => setView("result")}
        >
          ▦ Result
        </button>
        <span>
          {integer.format(artifact.row_count)} rows{artifact.truncated ? " · limited" : ""}
        </span>
      </div>
      {view === "chart" ? (
        <div className="bar-chart" aria-label={`Chart of ${artifact.columns[numericColumn]}`}>
          {chartRows.map((row, index) => (
            <div className="bar-column" key={index}>
              <span className="bar-value">{integer.format(Number(row[numericColumn]))}</span>
              <div
                className="bar"
                style={{
                  height: `${max === 0 ? 0 : Math.max(4, (Number(row[numericColumn]) / max) * 100)}%`,
                }}
              />
              <span className="bar-label">{String(row[0])}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {artifact.columns.map((column) => (
                  <th key={column}>{column.replaceAll("_", " ")}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {artifact.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}>{cell === null ? "—" : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ComparisonCard({
  artifact,
}: {
  artifact: Extract<Artifact, { type: "rule_comparison" }>;
}) {
  const deltaPercent = (value: number | null) => (value === null ? null : value * 100);
  const signed = (value: number, formatted: string) => `${value >= 0 ? "+" : ""}${formatted}`;
  const precisionDelta = deltaPercent(artifact.delta.precision);
  const recallDelta = deltaPercent(artifact.delta.recall);
  return (
    <section className="artifact-card comparison">
      <div className="artifact-heading">
        <span className="artifact-kicker">Rule comparison</span>
        <h3>Current vs previous</h3>
      </div>
      <div className="compare-rules">
        <div>
          <span>Current</span>
          <RuleCode>{artifact.current.rule}</RuleCode>
        </div>
        <div>
          <span>Previous</span>
          <RuleCode>{artifact.previous.rule}</RuleCode>
        </div>
      </div>
      <div className="metrics-grid">
        <Metric
          label="Precision"
          value={percent(artifact.current.metrics.precision)}
          change={precisionDelta}
          changeLabel={
            precisionDelta === null
              ? undefined
              : signed(precisionDelta, `${precisionDelta.toFixed(1)} pp`)
          }
        />
        <Metric
          label="Recall"
          value={percent(artifact.current.metrics.recall)}
          change={recallDelta}
          changeLabel={
            recallDelta === null ? undefined : signed(recallDelta, `${recallDelta.toFixed(1)} pp`)
          }
        />
        <Metric
          label="Flagged"
          value={integer.format(artifact.current.metrics.transactions_flagged)}
          change={artifact.delta.transactions_flagged}
          changeLabel={signed(
            artifact.delta.transactions_flagged,
            integer.format(artifact.delta.transactions_flagged),
          )}
        />
        <Metric
          label="Fraud caught"
          value={integer.format(artifact.current.metrics.fraud_caught)}
          change={artifact.delta.fraud_caught}
          changeLabel={signed(
            artifact.delta.fraud_caught,
            integer.format(artifact.delta.fraud_caught),
          )}
        />
        <Metric
          label="Value captured"
          value={money.format(artifact.current.metrics.fraud_value_captured_usd)}
          change={artifact.delta.fraud_value_captured_usd}
          changeLabel={signed(
            artifact.delta.fraud_value_captured_usd,
            money.format(artifact.delta.fraud_value_captured_usd),
          )}
        />
      </div>
      {(artifact.current.metrics.unlabelled_flagged > 0 ||
        artifact.previous.metrics.unlabelled_flagged > 0) && (
        <p className="artifact-note">
          Alert volume includes {integer.format(artifact.current.metrics.unlabelled_flagged)}
          {" current and "}
          {integer.format(artifact.previous.metrics.unlabelled_flagged)} previous unlabelled flagged
          transactions. Quality metrics use labelled data only.
        </p>
      )}
    </section>
  );
}

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  if (artifact.type === "sql")
    return (
      <details className="sql-card">
        <summary>View generated SQL</summary>
        <pre>
          <code>{artifact.sql}</code>
        </pre>
      </details>
    );
  if (artifact.type === "table") return <TableArtifact artifact={artifact} />;
  if (artifact.type === "backtest") return <BacktestCard result={artifact} />;
  if (artifact.type === "rule_comparison") return <ComparisonCard artifact={artifact} />;
  return (
    <section className={`artifact-card candidate ${artifact.valid ? "valid" : "invalid"}`}>
      <div className="artifact-heading">
        <span className="artifact-kicker">
          Candidate rule · {artifact.valid ? "Validated" : "Needs attention"}
        </span>
        <h3>Fraud hypothesis</h3>
      </div>
      {artifact.rule && <RuleCode>{artifact.rule}</RuleCode>}
      {artifact.errors.map((error) => (
        <p className="rule-error" key={error.code}>
          {error.message}
          {error.suggestion && ` ${error.suggestion}`}
        </p>
      ))}
    </section>
  );
}
