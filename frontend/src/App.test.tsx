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

function streamEvent(name: string, data: unknown): Uint8Array {
  return new TextEncoder().encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
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

  it("renders analytical artifacts before the final prose summary", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(chatResponse));
    render(<App />);

    await user.click(
      screen.getByRole("button", { name: /Which card type has the highest fraud rate/ }),
    );

    const artifact = await screen.findByLabelText("Chart of accounts");
    const summary = screen.getByRole("heading", { name: "Accounts linked" });
    expect(
      artifact.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders streamed text and collapses completed tool activity", async () => {
    const user = userEvent.setup();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            stream = controller;
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    render(<App />);
    await user.type(screen.getByLabelText("Ask Sherlock"), "Stream this{enter}");

    act(() => stream.enqueue(streamEvent("text_delta", { delta: "Working on " })));
    expect(await screen.findByText("Working on")).toBeInTheDocument();
    expect(screen.queryByText("Analyzing")).not.toBeInTheDocument();

    act(() =>
      stream.enqueue(
        streamEvent("tool_call", {
          id: "call-1",
          kind: "agent_handoff",
          name: "Data Analyst",
          message: "Handing off the analysis",
        }),
      ),
    );
    const activity = (await screen.findByText("Data Analyst")).closest("details");
    expect(activity).toHaveAttribute("open");
    expect(activity).toHaveTextContent("Running");

    act(() =>
      stream.enqueue(streamEvent("tool_result", { id: "call-1", message: "Analysis completed" })),
    );
    await waitFor(() => expect(activity).not.toHaveAttribute("open"));
    expect(activity).toHaveTextContent("Complete");

    act(() => {
      stream.enqueue(streamEvent("text_delta", { delta: "the result." }));
      stream.enqueue(streamEvent("complete", chatResponse));
      stream.close();
    });
    expect(await screen.findByRole("heading", { name: "Accounts linked" })).toBeInTheDocument();
    expect(screen.getByText("Analysis completed")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
      workingState: { candidate_rule: "amount_usd > 1000" },
    });
  });

  it("rolls back a partial assistant turn when the stream closes early", async () => {
    const user = userEvent.setup();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            stream = controller;
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    render(<App />);
    await user.type(screen.getByLabelText("Ask Sherlock"), "Interrupted stream{enter}");
    act(() => stream.enqueue(streamEvent("text_delta", { delta: "Partial answer" })));
    expect(await screen.findByText("Partial answer")).toBeInTheDocument();

    act(() => stream.close());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sherlock returned an invalid response",
    );
    expect(screen.queryByText("Partial answer")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Interrupted stream", { selector: ".user-message p" }),
    ).not.toBeInTheDocument();
  });

  it("renders GitHub-flavored Markdown tables in assistant messages", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        conversationId: "saved-id",
        title: "Fraud transaction value",
        messages: [
          {
            role: "assistant",
            content:
              "| Metric | Value |\n|---|---|\n| **Average Fraud Transaction Value** | **$85.59 USD** |",
          },
        ],
        workingState: {},
      }),
    );

    render(<App />);

    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Metric" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Value" })).toBeInTheDocument();
    const metric = within(table).getByRole("cell", {
      name: "Average Fraud Transaction Value",
    });
    const value = within(table).getByRole("cell", { name: "$85.59 USD" });
    expect(within(metric).getByText("Average Fraud Transaction Value").tagName).toBe("STRONG");
    expect(within(value).getByText("$85.59 USD").tagName).toBe("STRONG");
    expect(screen.queryByText("|---|---|")).not.toBeInTheDocument();
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

  it("rolls back a malformed success response and retries from the last valid state", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        conversationId: "saved-id",
        title: "Existing investigation",
        messages: [{ role: "assistant", content: "Existing answer" }],
        workingState: { last_sql: "SELECT 1" },
      }),
    );
    const malformedResponse = {
      ...chatResponse,
      metadata: undefined,
      working_state: { candidate_rule: "corrupted state" },
    };
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(malformedResponse))
      .mockResolvedValueOnce(jsonResponse({ ...chatResponse, artifacts: [] }));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Ask Sherlock"), "Malformed turn{enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Sherlock returned an invalid response");
    expect(
      screen.queryByText("Malformed turn", { selector: ".user-message p" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Existing answer")).toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "Dismiss" }));
    await user.type(screen.getByLabelText("Ask Sherlock"), "Retry{enter}");
    await screen.findByText("I found a concentrated pattern.");
    const retryPayload = JSON.parse(
      String((vi.mocked(fetch).mock.calls[1][1] as RequestInit).body),
    );
    expect(retryPayload.working_state).toEqual({ last_sql: "SELECT 1" });
    expect(retryPayload.history).toEqual([{ role: "assistant", content: "Existing answer" }]);
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
    const signal = (vi.mocked(fetch).mock.calls[0][1] as RequestInit).signal;

    await user.click(screen.getByRole("button", { name: "New investigation" }));
    expect(signal?.aborted).toBe(true);
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
