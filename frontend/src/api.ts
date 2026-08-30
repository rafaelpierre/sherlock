import type { ChatResponse, ConversationMessage, WorkingState } from "./types";

export class ChatApiError extends Error {}

export async function sendChat(
  conversationId: string,
  message: string,
  history: ConversationMessage[],
  workingState: WorkingState,
): Promise<ChatResponse> {
  const response = await fetch("/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      history: history.slice(-20),
      working_state: workingState,
    }),
  });
  if (!response.ok) {
    let detail = `Sherlock could not complete the request (${response.status}).`;
    try {
      const body = (await response.json()) as {
        detail?: { message?: string } | string;
        message?: string;
      };
      detail =
        typeof body.detail === "string"
          ? body.detail
          : (body.detail?.message ?? body.message ?? detail);
    } catch {
      // Preserve the status-based fallback when the server does not return JSON.
    }
    throw new ChatApiError(detail);
  }
  return (await response.json()) as ChatResponse;
}
