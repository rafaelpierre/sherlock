import { loadInvestigation, newInvestigation, saveInvestigation, STORAGE_KEY } from "./store";

describe("investigation persistence", () => {
  it("creates and saves bounded investigations", () => {
    const investigation = newInvestigation();
    expect(investigation.conversationId).toMatch(/00000000/);
    investigation.messages = Array.from({ length: 25 }, (_, index) => ({
      role: "user",
      content: String(index),
      artifacts: [
        { type: "table", columns: ["raw"], rows: [[index]], row_count: 1, truncated: false },
      ],
    }));
    saveInvestigation(investigation);
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored.messages).toHaveLength(20);
    expect(stored.messages[0]).not.toHaveProperty("artifacts");
  });

  it.each(["not json", JSON.stringify({ wrong: true })])(
    "recovers from invalid storage",
    (value) => {
      localStorage.setItem(STORAGE_KEY, value);
      expect(loadInvestigation().messages).toEqual([]);
    },
  );

  it("uses an empty working state when an older save omits it", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ conversationId: "id", messages: [] }));
    expect(loadInvestigation().workingState).toEqual({});
  });

  it("strips artifact payloads from older persisted transcripts", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        conversationId: "id",
        messages: [
          {
            role: "assistant",
            content: "Summary",
            artifacts: [{ type: "table", rows: [["raw"]] }],
          },
        ],
        workingState: {},
      }),
    );
    expect(loadInvestigation().messages[0]).toEqual({ role: "assistant", content: "Summary" });
  });
});
