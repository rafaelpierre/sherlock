export type Role = "user" | "assistant";

export interface ConversationMessage {
  role: Role;
  content: string;
}

export interface BacktestMetrics {
  population: number;
  labelled_population: number;
  fraud_total: number;
  transactions_flagged: number;
  unlabelled_flagged: number;
  fraud_caught: number;
  false_positives: number;
  false_negatives: number;
  true_negatives: number;
  precision: number | null;
  recall: number | null;
  false_positive_rate: number | null;
  fraud_value_total_usd: number;
  fraud_value_captured_usd: number;
  fraud_value_recall: number | null;
  alerts_per_day: number | null;
}

export interface BacktestResult {
  rule: string;
  metrics: BacktestMetrics;
}

export type Artifact =
  | { type: "sql"; sql: string }
  | { type: "table"; columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean }
  | {
      type: "candidate_rule";
      rule: string | null;
      valid: boolean;
      repair_count: number;
      errors: Array<{ code: string; message: string; suggestion: string | null }>;
    }
  | ({ type: "backtest" } & BacktestResult)
  | {
      type: "rule_comparison";
      current: BacktestResult;
      previous: BacktestResult;
      delta: Pick<
        BacktestMetrics,
        | "precision"
        | "recall"
        | "transactions_flagged"
        | "fraud_caught"
        | "fraud_value_captured_usd"
      >;
    };

export interface WorkingState {
  candidate_rule?: string | null;
  previous_rule?: string | null;
  last_sql?: string | null;
  last_backtest?: BacktestResult | null;
}

export interface ChatResponse {
  message: string;
  artifacts: Artifact[];
  working_state: WorkingState;
  metadata: { intent: string; repair_count: number; cache_hit: boolean };
}

export interface TranscriptMessage extends ConversationMessage {
  artifacts?: Artifact[];
  intent?: string;
}

export interface Investigation {
  conversationId: string;
  messages: TranscriptMessage[];
  workingState: WorkingState;
}
