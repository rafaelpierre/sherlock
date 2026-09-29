import type { BacktestMetrics, ChatResponse } from "../types";

export const metrics: BacktestMetrics = {
  population: 1000,
  labelled_population: 900,
  fraud_total: 100,
  transactions_flagged: 120,
  unlabelled_flagged: 3,
  fraud_caught: 80,
  false_positives: 40,
  false_negatives: 20,
  true_negatives: 760,
  precision: 0.666,
  recall: 0.8,
  false_positive_rate: 0.05,
  fraud_value_total_usd: 25000,
  fraud_value_captured_usd: 21000,
  fraud_value_recall: 0.84,
  alerts_per_day: 4.2,
};
export const chatResponse: ChatResponse = {
  message: "## Accounts linked\n\nI found a concentrated pattern.",
  artifacts: [
    {
      type: "sql",
      sql: "SELECT partner, COUNT(*) AS accounts FROM fraud_transactions GROUP BY partner",
    },
    {
      type: "table",
      columns: ["partner", "accounts"],
      rows: [
        ["NovaTech", 1600],
        ["Apex", 1300],
      ],
      row_count: 2,
      truncated: false,
    },
    { type: "candidate_rule", rule: "amount_usd > 1000", valid: true, repair_count: 0, errors: [] },
    { type: "backtest", rule: "amount_usd > 1000", metrics },
    {
      type: "rule_comparison",
      current: { rule: "amount_usd > 1000", metrics },
      previous: { rule: "amount_usd > 500", metrics: { ...metrics, precision: 0.5 } },
      delta: {
        precision: 0.166,
        recall: 0,
        transactions_flagged: -20,
        fraud_caught: 0,
        fraud_value_captured_usd: 100,
      },
    },
  ],
  working_state: { candidate_rule: "amount_usd > 1000" },
  metadata: { intent: "EXPLORE", repair_count: 0, cache_hit: false },
};
