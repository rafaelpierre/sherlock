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
