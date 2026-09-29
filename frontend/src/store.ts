import { z } from "zod";
import { workingStateSchema, type Investigation } from "./types";

export const STORAGE_KEY = "sherlock.conversation";
export const STORAGE_VERSION = 3;
const MAX_STORED_MESSAGES = 20;

const storedActivitySchema = z.strictObject({
  kind: z.enum(["tool_call", "agent_handoff"]),
  name: z.string().min(1).max(200),
  message: z.string().min(1).max(2_000),
  result: z.string().min(1).max(2_000),
  outcome: z.enum(["succeeded", "failed"]),
});

const storedMessageSchema = z.discriminatedUnion("role", [
  z.strictObject({
    role: z.literal("user"),
    content: z.string().min(1).max(10_000),
  }),
  z.strictObject({
    role: z.literal("assistant"),
    content: z.string().min(1).max(10_000),
    intro: z.string().min(1).max(10_000).optional(),
    activities: z.array(storedActivitySchema).max(50).optional(),
    outcome: z.enum(["complete", "failed"]).optional(),
  }),
]);

const storedInvestigationV1Schema = z.strictObject({
  version: z.literal(1),
  conversation_id: z.uuid(),
  messages: z.array(
    z.strictObject({
      role: z.enum(["user", "assistant"]),
      content: z.string().min(1).max(10_000),
    }),
  ),
  working_state: workingStateSchema,
});

const storedInvestigationSchema = z.strictObject({
  version: z.literal(STORAGE_VERSION),
  conversation_id: z.uuid(),
  messages: z.array(storedMessageSchema),
  working_state: workingStateSchema,
});

const storedInvestigationV2Schema = storedInvestigationSchema.extend({
  version: z.literal(2),
});

const storedPayloadSchema = z.discriminatedUnion("version", [
  storedInvestigationV1Schema,
  storedInvestigationV2Schema,
  storedInvestigationSchema,
]);

/** Start a client-owned investigation; the UUID has no server-side session. */
export function newInvestigation(): Investigation {
  return { conversationId: crypto.randomUUID(), messages: [], workingState: {} };
}

/**
 * Restore only the deliberately small, versioned continuity record.
 *
 * Parsing/migration failure clears the payload rather than trying to recover
 * stale rules or artifacts from arbitrary browser data.
 */
export function loadInvestigation(): Investigation {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return newInvestigation();
    const parsed = storedPayloadSchema.parse(JSON.parse(stored));
    const messages =
      parsed.version !== 1
        ? parsed.messages.slice(-MAX_STORED_MESSAGES).map((message) => ({
            ...message,
            id: crypto.randomUUID(),
            activities:
              message.role === "assistant"
                ? message.activities?.map((activity) => ({
                    ...activity,
                    id: crypto.randomUUID(),
                  }))
                : undefined,
          }))
        : parsed.messages.slice(-MAX_STORED_MESSAGES).map((message) => ({
            ...message,
            id: crypto.randomUUID(),
          }));
    return {
      conversationId: parsed.conversation_id,
      title: messages.find((message) => message.role === "user")?.content,
      messages,
      workingState: parsed.working_state,
    };
  } catch {
    clearInvestigation();
    return newInvestigation();
  }
}

/**
 * Persist safe continuity, never the evidence tables that rendered this turn.
 *
 * This keeps reload/retry useful without turning localStorage into an
 * unbounded transaction-data cache. Persistence is intentionally best effort.
 */
export function saveInvestigation(investigation: Investigation): void {
  try {
    const payload = storedInvestigationSchema.parse({
      version: STORAGE_VERSION,
      conversation_id: investigation.conversationId,
      messages: investigation.messages
        .slice(-MAX_STORED_MESSAGES)
        .map(({ role, content, intro, activities, outcome }) =>
          role === "user"
            ? { role, content }
            : {
                role,
                content,
                ...(intro && { intro }),
                ...(activities && {
                  activities: activities.map(
                    ({ kind, name, message, result, outcome: activityOutcome }) => ({
                      kind,
                      name,
                      message,
                      result: result ?? "This activity could not be completed.",
                      outcome: activityOutcome ?? "failed",
                    }),
                  ),
                }),
                ...(outcome && { outcome }),
              },
        ),
      working_state: investigation.workingState,
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Persistence is best-effort when browser storage is unavailable or full.
  }
}

export function clearInvestigation(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Reset still succeeds when browser storage is unavailable.
  }
}
