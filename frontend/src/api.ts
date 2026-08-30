import { chatResponseSchema, chatStreamEventSchema } from "./types";
import type { ChatResponse, ChatStreamEvent, ConversationMessage, WorkingState } from "./types";

export class ChatApiError extends Error {}

export interface ChatStreamHandlers {
  onEvent?: (event: Exclude<ChatStreamEvent, { type: "complete" | "error" }>) => void;
}

const invalidResponse = "Sherlock returned an invalid response. Please try again.";
const maxStreamActivities = 50;
const maxPendingEventCharacters = 256_000;

async function errorDetail(response: Response): Promise<string> {
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
  return detail;
}

function parseEvent(name: string, data: string): ChatStreamEvent {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    throw new ChatApiError(invalidResponse);
  }
  const candidate =
    name === "complete"
      ? { type: name, response: payload }
      : { type: name, ...(payload as object) };
  const result = chatStreamEventSchema.safeParse(candidate);
  if (!result.success) throw new ChatApiError(invalidResponse);
  return result.data;
}

async function readEventStream(
  response: Response,
  handlers: ChatStreamHandlers,
): Promise<ChatResponse> {
  if (!response.body) throw new ChatApiError(invalidResponse);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];
  let pendingDataLength = 0;
  let completed: ChatResponse | undefined;
  let reachedEof = false;
  let streamedTextLength = 0;
  const activeCalls = new Set<string>();
  const completedCalls = new Set<string>();

  function dispatch() {
    if (dataLines.length === 0) {
      eventName = "";
      pendingDataLength = 0;
      return;
    }
    const event = parseEvent(eventName || "message", dataLines.join("\n"));
    eventName = "";
    dataLines = [];
    pendingDataLength = 0;
    if (completed) throw new ChatApiError(invalidResponse);
    if (event.type === "error") throw new ChatApiError(event.message);
    if (event.type === "complete") {
      if (activeCalls.size !== completedCalls.size) throw new ChatApiError(invalidResponse);
      completed = event.response;
    } else {
      if (event.type === "text_delta") {
        streamedTextLength += event.delta.length;
        if (streamedTextLength > 10_000) throw new ChatApiError(invalidResponse);
      }
      if (event.type === "tool_call") {
        if (activeCalls.has(event.id) || activeCalls.size >= maxStreamActivities) {
          throw new ChatApiError(invalidResponse);
        }
        activeCalls.add(event.id);
      }
      if (event.type === "tool_result") {
        if (!activeCalls.has(event.id) || completedCalls.has(event.id)) {
          throw new ChatApiError(invalidResponse);
        }
        completedCalls.add(event.id);
      }
      handlers.onEvent?.(event);
    }
  }

  function line(value: string) {
    if (!value) {
      dispatch();
      return;
    }
    if (value.startsWith(":")) return;
    const separator = value.indexOf(":");
    const field = separator === -1 ? value : value.slice(0, separator);
    let fieldValue = separator === -1 ? "" : value.slice(separator + 1);
    if (fieldValue.startsWith(" ")) fieldValue = fieldValue.slice(1);
    if (field === "event") eventName = fieldValue;
    if (field === "data") {
      pendingDataLength += fieldValue.length + 1;
      if (pendingDataLength > maxPendingEventCharacters) throw new ChatApiError(invalidResponse);
      dataLines.push(fieldValue);
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      reachedEof = done;
      buffer += decoder.decode(value, { stream: !done });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        if (newline > maxPendingEventCharacters) throw new ChatApiError(invalidResponse);
        const current = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        line(current);
        newline = buffer.indexOf("\n");
      }
      if (buffer.length > maxPendingEventCharacters) throw new ChatApiError(invalidResponse);
      if (completed) return completed;
      if (done) break;
    }
    if (buffer) line(buffer.replace(/\r$/, ""));
    dispatch();
    if (!completed) throw new ChatApiError(invalidResponse);
    return completed;
  } finally {
    if (!reachedEof) await reader.cancel().catch(() => undefined);
  }
}

export async function sendChat(
  conversationId: string,
  message: string,
  history: ConversationMessage[],
  workingState: WorkingState,
  handlers: ChatStreamHandlers = {},
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await fetch("/v1/chat", {
    method: "POST",
    headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      history: history.slice(-20),
      working_state: workingState,
    }),
    signal,
  });
  if (!response.ok) {
    throw new ChatApiError(await errorDetail(response));
  }
  if (response.headers.get("Content-Type")?.includes("text/event-stream")) {
    return readEventStream(response, handlers);
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ChatApiError(invalidResponse);
  }
  const result = chatResponseSchema.safeParse(body);
  if (!result.success) {
    throw new ChatApiError(invalidResponse);
  }
  return result.data;
}
