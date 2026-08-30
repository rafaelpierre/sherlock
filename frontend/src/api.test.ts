import { ChatApiError, sendChat } from "./api";
import { chatResponse } from "./test/fixtures";

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
});
