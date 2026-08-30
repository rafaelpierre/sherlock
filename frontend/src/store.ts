import type { Investigation } from "./types";

export const STORAGE_KEY = "sherlock.conversation";

export function newInvestigation(): Investigation {
  return { conversationId: crypto.randomUUID(), messages: [], workingState: {} };
}

export function loadInvestigation(): Investigation {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return newInvestigation();
    const parsed = JSON.parse(stored) as Investigation;
    if (!parsed.conversationId || !Array.isArray(parsed.messages)) return newInvestigation();
    return {
      ...parsed,
      title: parsed.title ?? parsed.messages.find((message) => message.role === "user")?.content,
      messages: parsed.messages.slice(-20).map(({ id, role, content }) => ({
        id: id ?? crypto.randomUUID(),
        role,
        content,
      })),
      workingState: parsed.workingState ?? {},
    };
  } catch {
    return newInvestigation();
  }
}

export function saveInvestigation(investigation: Investigation): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...investigation,
        messages: investigation.messages.slice(-20).map(({ id, role, content }) => ({
          id: id ?? crypto.randomUUID(),
          role,
          content,
        })),
      }),
    );
  } catch {
    // Persistence is best-effort when browser storage is unavailable or full.
  }
}
