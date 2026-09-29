import { ChatApiError, sendChat } from "./api";
import { chatResponse } from "./test/fixtures";
import type { ChatResponse } from "./types";

type ResponseMutation = (response: ChatResponse) => unknown;

const invalidResponses: Array<[string, ResponseMutation]> = [
  [
    "missing top-level data",
    (response) => ({
      artifacts: response.artifacts,
      working_state: response.working_state,
      metadata: response.metadata,
    }),
  ],
  [
    "invalid metadata",
    (response) => ({ ...response, metadata: { ...response.metadata, repair_count: "zero" } }),
  ],
  [
    "missing metadata",
    (response) => ({
      message: response.message,
      artifacts: response.artifacts,
      working_state: response.working_state,
    }),
  ],
  [
    "missing working state",
    (response) => ({
      message: response.message,
      artifacts: response.artifacts,
      metadata: response.metadata,
    }),
  ],
  [
    "invalid working state",
    (response) => ({
      ...response,
      working_state: { last_backtest: { rule: "", metrics: {} } },
    }),
  ],
];

function eventStreamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream; charset=utf-8" } },
  );
}

function event(name: string, data: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
}

describe("chat API", () => {
  it("returns typed successful responses and caps history", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(chatResponse), { status: 200 }),
    );
    const history = Array.from({ length: 30 }, (_, index) => ({
      role: "user" as const,
      content: String(index),
    }));
    await expect(sendChat("id", "question", history, {})).resolves.toEqual(chatResponse);
    const payload = JSON.parse(String((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body));
    expect(payload.history).toHaveLength(20);
    expect(payload.history[0].content).toBe("10");
    expect((vi.mocked(fetch).mock.calls[0][1] as RequestInit).headers).toMatchObject({
      Accept: "text/event-stream",
    });
  });

  it("parses split SSE events and returns the authoritative completion", async () => {
    const payload = [
      ": heartbeat\r\n\r\n",
      event("text_delta", { delta: "Accounts " }),
      event("tool_call", {
        id: "call-1",
        kind: "tool_call",
        name: "Text2SQL",
        message: "Querying transactions",
      }),
      event("tool_result", { id: "call-1", message: "Found 2 rows", outcome: "succeeded" }),
      event("text_delta", { delta: "linked" }),
      event("complete", chatResponse),
    ].join("");
    const splitAt = [7, 31, 86, 151];
    const chunks: string[] = [];
    let offset = 0;
    for (const end of splitAt) {
      chunks.push(payload.slice(offset, end));
      offset = end;
    }
    chunks.push(payload.slice(offset));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(eventStreamResponse(chunks));
    const onEvent = vi.fn();

    await expect(sendChat("id", "question", [], {}, { onEvent })).resolves.toEqual(chatResponse);
    expect(onEvent.mock.calls.map(([streamEvent]) => streamEvent)).toEqual([
      { type: "text_delta", delta: "Accounts ", segment: "content" },
      {
        type: "tool_call",
        id: "call-1",
        kind: "tool_call",
        name: "Text2SQL",
        message: "Querying transactions",
      },
      { type: "tool_result", id: "call-1", message: "Found 2 rows", outcome: "succeeded" },
      { type: "text_delta", delta: "linked", segment: "content" },
    ]);
  });

  it.each([
    ["closes before completion", event("text_delta", { delta: "Partial" })],
    ["contains malformed JSON", "event: text_delta\ndata: {broken}\n\n"],
    ["contains an unknown event", event("future_event", { value: true })],
    [
      "returns a result without a call",
      event("tool_result", { id: "missing", message: "Done", outcome: "succeeded" }),
    ],
    [
      "completes with an active call",
      event("tool_call", {
        id: "active",
        kind: "tool_call",
        name: "Text2SQL",
        message: "Querying transactions",
      }) + event("complete", chatResponse),
    ],
  ])("rejects a stream that %s", async (_label, payload) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(eventStreamResponse([payload]));
    await expect(sendChat("id", "question", [], {})).rejects.toEqual(
      new ChatApiError("Sherlock returned an invalid response. Please try again."),
    );
  });

  it("surfaces an SSE error event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      eventStreamResponse([event("error", { message: "Analysis timed out." })]),
    );
    await expect(sendChat("id", "question", [], {})).rejects.toEqual(
      new ChatApiError("Analysis timed out."),
    );
  });

  it("cancels a non-closing response after a terminal stream event", async () => {
    const encoder = new TextEncoder();
    const cancel = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(event("error", { message: "Analysis timed out." })));
          },
          cancel,
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    await expect(sendChat("id", "question", [], {})).rejects.toEqual(
      new ChatApiError("Analysis timed out."),
    );
    expect(cancel).toHaveBeenCalledOnce();
  });

  it.each([
    ["unterminated line", "x".repeat(256_001)],
    [
      "unterminated data fields",
      `event: text_delta\n${Array.from({ length: 257 }, () => `data: ${"x".repeat(1_000)}\n`).join("")}`,
    ],
  ])("rejects and cancels an oversized %s", async (_label, payload) => {
    const encoder = new TextEncoder();
    const cancel = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(payload));
          },
          cancel,
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    await expect(sendChat("id", "question", [], {})).rejects.toEqual(
      new ChatApiError("Sherlock returned an invalid response. Please try again."),
    );
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("caps the number of streamed activities", async () => {
    const activities = Array.from({ length: 51 }, (_, index) =>
      [
        event("tool_call", {
          id: `call-${index}`,
          kind: "tool_call",
          name: "Text2SQL",
          message: "Querying transactions",
        }),
        event("tool_result", { id: `call-${index}`, message: "Done", outcome: "succeeded" }),
      ].join(""),
    ).join("");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(eventStreamResponse([activities]));

    await expect(sendChat("id", "question", [], {})).rejects.toEqual(
      new ChatApiError("Sherlock returned an invalid response. Please try again."),
    );
  });

  it.each([
    [{ detail: "Plain detail" }, "Plain detail"],
    [{ message: "Top-level message" }, "Top-level message"],
    [{}, "Sherlock could not complete the request (500)."],
  ])("surfaces structured API failures", async (body, expected) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status: 500 }),
    );
    await expect(sendChat("id", "question", [], {})).rejects.toEqual(new ChatApiError(expected));
  });

  it("falls back when an error response is not JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("broken", { status: 503 }));
    await expect(sendChat("id", "question", [], {})).rejects.toThrow("(503)");
  });

  it("rejects invalid JSON in a successful response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("broken", { status: 200 }));
    await expect(sendChat("id", "question", [], {})).rejects.toEqual(
      new ChatApiError("Sherlock returned an invalid response. Please try again."),
    );
  });

  it.each(invalidResponses)("rejects %s", async (_label, mutate) => {
    const malformed = mutate(structuredClone(chatResponse));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(malformed), { status: 200 }),
    );
    await expect(sendChat("id", "question", [], {})).rejects.toBeInstanceOf(ChatApiError);
  });

  it.each([
    ["sql", { type: "sql", sql: 42 }],
    [
      "table",
      { type: "table", columns: ["account"], rows: "not rows", row_count: 1, truncated: false },
    ],
    [
      "analysis_step",
      {
        type: "analysis_step",
        step: 1,
        question: "Compare fraud rates",
        sql: "SELECT 1",
        table: { columns: ["rate"], rows: "not rows", row_count: 1, truncated: false },
      },
    ],
    [
      "candidate_rule",
      {
        type: "candidate_rule",
        rule: "amount_usd > 1000",
        valid: false,
        repair_count: 0,
        errors: [{ code: "INVALID", message: "Invalid rule", suggestion: 42 }],
      },
    ],
    ["backtest", { type: "backtest", rule: "amount_usd > 1000", metrics: {} }],
    [
      "rule_comparison",
      {
        type: "rule_comparison",
        current: { rule: "current", metrics: {} },
        previous: { rule: "previous", metrics: {} },
        delta: {},
      },
    ],
  ])("rejects a malformed %s artifact", async (_type, artifact) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ...chatResponse, artifacts: [artifact] }), { status: 200 }),
    );
    await expect(sendChat("id", "question", [], {})).rejects.toBeInstanceOf(ChatApiError);
  });

  it("rejects unknown future artifact kinds safely", async () => {
    const response = {
      ...chatResponse,
      artifacts: [{ type: "future_visualization", specification: {} }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(response), { status: 200 }),
    );
    await expect(sendChat("id", "question", [], {})).rejects.toBeInstanceOf(ChatApiError);
  });
});
