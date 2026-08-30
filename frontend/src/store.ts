import { z } from "zod";
import { workingStateSchema, type Investigation } from "./types";

export const STORAGE_KEY = "sherlock.conversation";
export const STORAGE_VERSION = 1;
const MAX_STORED_MESSAGES = 20;

const storedMessageSchema = z.strictObject({
  role: z.enum(["user", "assistant"]),
  content: z.string().min(1).max(10_000),
});

const storedInvestigationSchema = z.strictObject({
  version: z.literal(STORAGE_VERSION),
  conversation_id: z.uuid(),
  messages: z.array(storedMessageSchema),
  working_state: workingStateSchema,
});

export function newInvestigation(): Investigation {
  return { conversationId: crypto.randomUUID(), messages: [], workingState: {} };
}

export function loadInvestigation(): Investigation {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return newInvestigation();
    const parsed = storedInvestigationSchema.parse(JSON.parse(stored));
    const messages = parsed.messages.slice(-MAX_STORED_MESSAGES).map(({ role, content }) => ({
      id: crypto.randomUUID(),
      role,
      content,
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

export function saveInvestigation(investigation: Investigation): void {
  try {
    const payload = storedInvestigationSchema.parse({
      version: STORAGE_VERSION,
      conversation_id: investigation.conversationId,
      messages: investigation.messages
        .slice(-MAX_STORED_MESSAGES)
        .map(({ role, content }) => ({ role, content })),
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
