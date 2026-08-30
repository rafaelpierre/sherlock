import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { STORAGE_KEY } from "./store";
import { chatResponse } from "./test/fixtures";

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("Sherlock application", () => {
  it("renders the empty state and submits with Enter", async () => {
    const user = userEvent.setup();
    let resolveRequest!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    render(<App />);

    expect(
      screen.getByRole("heading", { name: /what would you like to uncover/i }),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Ask Sherlock"), "Find suspicious partners{enter}");
    expect(screen.getByRole("status")).toHaveTextContent("Data Analyst");
    expect(
      screen.getByText("Find suspicious partners", { selector: ".activity-prompt" }),
    ).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({ messages: [] });

    await act(async () => resolveRequest(jsonResponse(chatResponse)));
    expect(await screen.findByRole("heading", { name: "Accounts linked" })).toBeInTheDocument();
    expect(screen.getByText("Fraud hypothesis")).toBeInTheDocument();
    expect(screen.getByText("Historical replay")).toBeInTheDocument();
    expect(screen.getByText("Current vs previous")).toBeInTheDocument();
    expect(screen.getByText("+16.6 pp")).toBeInTheDocument();
    expect(screen.getByText("+$100")).toBeInTheDocument();
    expect(
      screen.getByText(/Alert volume includes 3 current and 3 previous unlabelled flagged/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Chart of accounts")).toBeInTheDocument();

    const request = vi.mocked(fetch).mock.calls[0];
    expect(request[0]).toBe("/v1/chat");
    expect(JSON.parse(String((request[1] as RequestInit).body))).toMatchObject({
      message: "Find suspicious partners",
      history: [],
      working_state: {},
    });
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
      workingState: { candidate_rule: "amount_usd > 1000" },
    });
  });

  it("submits a suggestion and supports result table and SQL interactions", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(chatResponse));
    render(<App />);
    await user.click(
      screen.getByRole("button", { name: /Which card type has the highest fraud rate/ }),
    );
    expect(await screen.findByText("I found a concentrated pattern.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "▦ Result" }));
    expect(screen.getByRole("cell", { name: "NovaTech" })).toBeInTheDocument();
    await user.click(screen.getByText("View generated SQL"));
    expect(screen.getByText(/SELECT partner/)).toBeVisible();
  });

  it("keeps Shift+Enter as a newline and blocks blank submissions", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<App />);
    const input = screen.getByLabelText("Ask Sherlock");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.type(input, "line one{shift>}{enter}{/shift}line two");
    expect(input).toHaveValue("line one\nline two");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects overlong questions before adding them to the investigation", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<App />);
    const input = screen.getByLabelText("Ask Sherlock");
    fireEvent.change(input, { target: { value: "x".repeat(2_001) } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("alert")).toHaveTextContent("2,000 characters or fewer");
    expect(
      screen.getByRole("heading", { name: /what would you like to uncover/i }),
    ).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows API errors, dismisses them, and starts a new investigation", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: { message: "Candidate state is missing." } }, { status: 422 }),
    );
    render(<App />);
    await user.type(screen.getByLabelText("Ask Sherlock"), "Backtest it{enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Candidate state is missing.");
    await user.click(within(alert).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New investigation" }));
    expect(
      screen.getByRole("heading", { name: /what would you like to uncover/i }),
    ).toBeInTheDocument();
  });

  it("restores the title after an initial request fails", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "Temporary failure" }, { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ ...chatResponse, artifacts: [] }));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Ask Sherlock"), "Failed question{enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Temporary failure");
    await user.click(within(alert).getByRole("button", { name: "Dismiss" }));
    await user.type(screen.getByLabelText("Ask Sherlock"), "Successful question{enter}");

    expect(await screen.findByRole("heading", { name: "Successful question" })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
      title: "Successful question",
    });
  });

  it("discards an in-flight response when starting a new investigation", async () => {
    const user = userEvent.setup();
    let resolveRequest!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    render(<App />);
    await user.type(screen.getByLabelText("Ask Sherlock"), "Old question{enter}");
    expect(screen.getByRole("status")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New investigation" }));
    expect(
      screen.getByRole("heading", { name: /what would you like to uncover/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Ask Sherlock")).not.toBeDisabled();
    await act(async () => resolveRequest(jsonResponse(chatResponse)));
    expect(screen.queryByText("I found a concentrated pattern.")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /what would you like to uncover/i }),
    ).toBeInTheDocument();
  });

  it("rolls failed turns out of bounded retry history", async () => {
    const messages = Array.from({ length: 20 }, (_, index) => ({
      id: `message-${index}`,
      role: (index % 2 ? "assistant" : "user") as "user" | "assistant",
      content: `Bounded ${index}`,
    }));
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ conversationId: "saved-id", title: "Bounded", messages, workingState: {} }),
    );
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "Temporary failure" }, { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ ...chatResponse, artifacts: [] }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Ask Sherlock"), "Failed turn{enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Temporary failure");
    expect(screen.getByText("Bounded 0")).toBeInTheDocument();
    expect(
      screen.queryByText("Failed turn", { selector: ".user-message p" }),
    ).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "Dismiss" }));
    await user.type(screen.getByLabelText("Ask Sherlock"), "Retry{enter}");
    await screen.findByText("I found a concentrated pattern.");
    const retryPayload = JSON.parse(
      String((vi.mocked(fetch).mock.calls[1][1] as RequestInit).body),
    );
    expect(retryPayload.history).toHaveLength(20);
    expect(retryPayload.history).not.toContainEqual(
      expect.objectContaining({ content: "Failed turn" }),
    );
  });

  it("restores a saved investigation and sends bounded plain history", async () => {
    const messages = Array.from({ length: 22 }, (_, index) => ({
      role: (index % 2 ? "assistant" : "user") as "user" | "assistant",
      content: `Message ${index}`,
    }));
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        conversationId: "saved-id",
        title: "Original investigation",
        messages,
        workingState: { last_sql: "SELECT 1" },
      }),
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ ...chatResponse, artifacts: [] }),
    );
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByRole("heading", { name: "Original investigation" })).toBeInTheDocument();
    expect(screen.queryByText("Message 0")).not.toBeInTheDocument();
    expect(screen.getByText("Message 21")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Ask Sherlock"), "Next{enter}");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const payload = JSON.parse(String((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body));
    expect(payload.history).toHaveLength(20);
    expect(payload.working_state.last_sql).toBe("SELECT 1");
  });
});
