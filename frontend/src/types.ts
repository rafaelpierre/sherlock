import { z } from "zod";

export type Role = "user" | "assistant";
export type ActivityOutcome = "succeeded" | "failed";

export interface ConversationMessage {
  role: Role;
  content: string;
  activities?: ConversationActivity[];
  outcome?: "complete" | "failed";
}

export interface ConversationActivity {
  kind: "tool_call" | "agent_handoff";
  name: string;
  message: string;
  result: string;
  outcome: ActivityOutcome;
}

const nullableNumber = z.number().nullable();

const backtestMetricsSchema = z.strictObject({
  population: z.number().int(),
  labelled_population: z.number().int(),
  fraud_total: z.number().int(),
  transactions_flagged: z.number().int(),
  unlabelled_flagged: z.number().int(),
  fraud_caught: z.number().int(),
  false_positives: z.number().int(),
  false_negatives: z.number().int(),
  true_negatives: z.number().int(),
  precision: nullableNumber,
  recall: nullableNumber,
  false_positive_rate: nullableNumber,
  fraud_value_total_usd: z.number(),
  fraud_value_captured_usd: z.number(),
  fraud_value_recall: nullableNumber,
  alerts_per_day: nullableNumber,
});

const backtestResultSchema = z.strictObject({
  rule: z.string(),
  metrics: backtestMetricsSchema,
});

const queryDataSchema = z.strictObject({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number().int(),
  truncated: z.boolean(),
});

const artifactSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("sql"), sql: z.string().min(1) }),
  z.strictObject({
    type: z.literal("table"),
    ...queryDataSchema.shape,
  }),
  z.strictObject({
    type: z.literal("analysis_step"),
    step: z.number().int().min(1),
    question: z.string().min(1).max(2_000),
    sql: z.string().min(1).max(20_000),
    table: queryDataSchema,
  }),
  z.strictObject({
    type: z.literal("candidate_rule"),
    rule: z.string().nullable(),
    valid: z.boolean(),
    repair_count: z.number().int().min(0).max(2),
    errors: z.array(
      z.strictObject({
        code: z.string(),
        message: z.string(),
        suggestion: z.string().nullable(),
      }),
    ),
  }),
  z.strictObject({ type: z.literal("backtest"), ...backtestResultSchema.shape }),
  z.strictObject({
    type: z.literal("rule_comparison"),
    current: backtestResultSchema,
    previous: backtestResultSchema,
    delta: z.strictObject({
      precision: nullableNumber,
      recall: nullableNumber,
      transactions_flagged: z.number().int(),
      fraud_caught: z.number().int(),
      fraud_value_captured_usd: z.number(),
    }),
  }),
]);

const boundedStateText = z
  .string()
  .max(20_000)
  .refine((value) => value.trim().length > 0);

const storedBacktestSchema = z.strictObject({
  rule: boundedStateText,
  metrics: backtestMetricsSchema,
});

export const workingStateSchema = z.strictObject({
  candidate_rule: boundedStateText.nullable().optional(),
  previous_rule: boundedStateText.nullable().optional(),
  last_sql: boundedStateText.nullable().optional(),
  last_backtest: storedBacktestSchema.nullable().optional(),
});

export const chatResponseSchema = z.strictObject({
  message: z
    .string()
    .min(1)
    .max(10_000)
    .refine((value) => value.trim().length > 0),
  artifacts: z.array(artifactSchema),
  working_state: workingStateSchema,
  metadata: z.strictObject({
    intent: z.enum(["EXPLORE", "GENERATE_RULE", "REFINE_RULE", "BACKTEST_RULE", "COMPARE_RULES"]),
    repair_count: z.number().int().min(0),
    cache_hit: z.boolean(),
  }),
});

const streamActivityIdSchema = z.string().min(1).max(200);
const streamActivityNameSchema = z.string().min(1).max(200);
const streamActivityMessageSchema = z.string().min(1).max(2_000);

export const chatStreamEventSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("text_delta"), delta: z.string().min(1).max(10_000) }),
  z.strictObject({
    type: z.literal("tool_call"),
    id: streamActivityIdSchema,
    kind: z.enum(["tool_call", "agent_handoff"]),
    name: streamActivityNameSchema,
    message: streamActivityMessageSchema,
  }),
  z.strictObject({
    type: z.literal("tool_result"),
    id: streamActivityIdSchema,
    message: streamActivityMessageSchema,
    outcome: z.enum(["succeeded", "failed"]),
  }),
  z.strictObject({ type: z.literal("complete"), response: chatResponseSchema }),
  z.strictObject({ type: z.literal("error"), message: streamActivityMessageSchema }),
]);

export type BacktestMetrics = z.infer<typeof backtestMetricsSchema>;
export type BacktestResult = z.infer<typeof backtestResultSchema>;
export type Artifact = z.infer<typeof artifactSchema>;
export type WorkingState = z.infer<typeof workingStateSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type ChatStreamEvent = z.infer<typeof chatStreamEventSchema>;
export type StreamActivity = Extract<ChatStreamEvent, { type: "tool_call" }> & {
  result?: string;
  outcome?: ActivityOutcome;
};

export interface TranscriptMessage {
  id?: string;
  role: Role;
  content: string;
  intro?: string;
  artifacts?: Artifact[];
  intent?: string;
  activities?: StreamActivity[];
  outcome?: "complete" | "failed";
}

export interface Investigation {
  conversationId: string;
  title?: string;
  messages: TranscriptMessage[];
  workingState: WorkingState;
}
