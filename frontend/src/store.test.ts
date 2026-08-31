import {
  clearInvestigation,
  loadInvestigation,
  newInvestigation,
  saveInvestigation,
  STORAGE_KEY,
  STORAGE_VERSION,
} from "./store";
import { metrics } from "./test/fixtures";

const CONVERSATION_ID = "3b621bd5-98dd-4be0-b713-89b1ac751fab";

function storedPayload(overrides: Record<string, unknown> = {}) {
  return {
    version: STORAGE_VERSION,
    conversation_id: CONVERSATION_ID,
    messages: [],
    working_state: {},
    ...overrides,
  };
}

describe("investigation persistence", () => {
  it("writes a versioned, bounded allowlist while retaining authoritative working state", () => {
    const investigation = newInvestigation();
    expect(investigation.conversationId).toMatch(/00000000/);
    investigation.title = "Runtime-only title";
    investigation.workingState = {
      candidate_rule: "amount_usd > 1000",
      previous_rule: null,
      last_sql: "SELECT amount_usd FROM fraud_transactions",
      last_backtest: { rule: "amount_usd > 1000", metrics },
    };
    investigation.messages = Array.from({ length: 25 }, (_, index) => ({
      id: `message-${index}`,
      role: "user",
      content: String(index),
      artifacts: [
        { type: "table", columns: ["raw"], rows: [[index]], row_count: 1, truncated: false },
      ],
    }));

    saveInvestigation(investigation);

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored).toEqual({
      version: STORAGE_VERSION,
      conversation_id: investigation.conversationId,
      messages: Array.from({ length: 20 }, (_, index) => ({
        role: "user",
        content: String(index + 5),
      })),
      working_state: investigation.workingState,
    });
    expect(JSON.stringify(stored)).not.toContain("raw");
    expect(stored).not.toHaveProperty("title");
  });

  it("restores the latest messages, derives the title, and retains working state", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(
        storedPayload({
          messages: Array.from({ length: 22 }, (_, index) => ({
            role: index % 2 ? "assistant" : "user",
            content: `Message ${index}`,
          })),
          working_state: { candidate_rule: "amount_usd > 1000" },
        }),
      ),
    );

    const restored = loadInvestigation();

    expect(restored).toMatchObject({
      conversationId: CONVERSATION_ID,
      title: "Message 2",
      workingState: { candidate_rule: "amount_usd > 1000" },
    });
    expect(restored.messages).toHaveLength(20);
    expect(restored.messages[0]).toMatchObject({ role: "user", content: "Message 2" });
    expect(restored.messages[0].id).toBeTruthy();
  });

  it("migrates v1 prose history and round-trips bounded activity failures", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...storedPayload(),
        version: 1,
        messages: [{ role: "user", content: "Explore further" }],
      }),
    );
    const investigation = loadInvestigation();
    investigation.messages.push({
      id: "failed-turn",
      role: "assistant",
      content: "Sherlock could not complete the request. Please try again.",
      outcome: "failed",
      activities: [
        {
          id: "activity-1",
          type: "tool_call",
          kind: "agent_handoff",
          name: "Transaction analysis",
          message: "Sherlock is investigating the transaction data.",
          result: "This activity could not be completed.",
          outcome: "failed",
        },
      ],
      artifacts: [{ type: "table", columns: ["raw"], rows: [[1]], row_count: 1, truncated: false }],
    });

    saveInvestigation(investigation);

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored).toMatchObject({
      version: STORAGE_VERSION,
      messages: [
        { role: "user", content: "Explore further" },
        {
          role: "assistant",
          outcome: "failed",
          activities: [{ name: "Transaction analysis", outcome: "failed" }],
        },
      ],
    });
    expect(JSON.stringify(stored)).not.toContain('"artifacts"');
    expect(JSON.stringify(stored)).not.toContain('"rows"');
  });

  it("migrates v2 history and preserves a streamed introduction", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...storedPayload(),
        version: 2,
        messages: [{ role: "assistant", content: "Result summary" }],
      }),
    );
    const investigation = loadInvestigation();
    investigation.messages[0].intro = "I will investigate this.";

    saveInvestigation(investigation);

    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
      version: STORAGE_VERSION,
      messages: [
        {
          role: "assistant",
          intro: "I will investigate this.",
          content: "Result summary",
        },
      ],
    });
  });

  it.each([
    ["malformed JSON", "not json"],
    ["missing fields", JSON.stringify({ version: STORAGE_VERSION })],
    ["an incompatible version", JSON.stringify(storedPayload({ version: 4 }))],
    ["an incompatible shape", JSON.stringify(storedPayload({ unexpected: true }))],
    [
      "raw artifacts attached to a message",
      JSON.stringify(
        storedPayload({
          messages: [{ role: "assistant", content: "Summary", artifacts: [{ rows: [[1]] }] }],
        }),
      ),
    ],
  ])("discards %s and recovers with an empty investigation", (_label, value) => {
    localStorage.setItem(STORAGE_KEY, value);

    expect(loadInvestigation()).toMatchObject({ messages: [], workingState: {} });
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("falls back when browser storage cannot be read", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementationOnce(() => {
      throw new DOMException("Access denied", "SecurityError");
    });
    expect(loadInvestigation()).toMatchObject({ messages: [], workingState: {} });
  });

  it("continues when browser storage cannot be written", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });
    expect(() => saveInvestigation(newInvestigation())).not.toThrow();
  });

  it("continues when browser storage cannot be cleared", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementationOnce(() => {
      throw new DOMException("Access denied", "SecurityError");
    });
    expect(clearInvestigation).not.toThrow();
  });
});
