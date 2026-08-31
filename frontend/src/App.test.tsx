import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { STORAGE_KEY, STORAGE_VERSION } from "./store";
import { chatResponse } from "./test/fixtures";

const SAVED_CONVERSATION_ID = "3b621bd5-98dd-4be0-b713-89b1ac751fab";

function storedInvestigation(
  messages: { role: "user" | "assistant"; content: string }[],
  workingState: Record<string, unknown> = {},
) {
  return {
    version: STORAGE_VERSION,
    conversation_id: SAVED_CONVERSATION_ID,
    messages: messages.map(({ role, content }) => ({ role, content })),
    working_state: workingState,
  };
}

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
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();

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
      version: STORAGE_VERSION,
      conversation_id: expect.any(String),
      working_state: { candidate_rule: "amount_usd > 1000" },
    });
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("NovaTech");
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
    const scrollSpy = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
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
    await waitFor(() => {
      expect(scrollSpy.mock.instances.at(-1)).toBe(artifact.closest(".artifacts"));
      expect(scrollSpy).toHaveBeenLastCalledWith({ behavior: "smooth", block: "start" });
    });
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
      stream.enqueue(
        streamEvent("tool_result", {
          id: "call-1",
          message: "Analysis completed",
          outcome: "succeeded",
        }),
      ),
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
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
        working_state: { candidate_rule: "amount_usd > 1000" },
      }),
    );
  });

  it("keeps the streamed introduction before evidence and the completed summary last", async () => {
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

    act(() => stream.enqueue(streamEvent("text_delta", { delta: "I will investigate this." })));
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
    act(() =>
      stream.enqueue(
        streamEvent("tool_result", {
          id: "call-1",
          message: "Analysis completed",
          outcome: "succeeded",
        }),
      ),
    );
    act(() => {
      stream.enqueue(streamEvent("text_delta", { delta: "The result is ready. Any follow-up?" }));
      stream.enqueue(
        streamEvent("complete", {
          ...chatResponse,
          message: "The result is ready. Any follow-up?",
        }),
      );
      stream.close();
    });

    const introduction = await screen.findByText("I will investigate this.");
    const activity = screen.getByText("Data Analyst");
    const artifact = screen.getByLabelText("Chart of accounts");
    const summary = screen.getByText("The result is ready. Any follow-up?");
    expect(
      introduction.compareDocumentPosition(activity) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      activity.compareDocumentPosition(artifact) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      artifact.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
      messages: [
        expect.objectContaining({ content: "Stream this" }),
        expect.objectContaining({
          intro: "I will investigate this.",
          content: "The result is ready. Any follow-up?",
        }),
      ],
    });
  });

  it("persists a failed streamed turn with its activity summaries", async () => {
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
    act(() =>
      stream.enqueue(
        streamEvent("tool_call", {
          id: "call-1",
          kind: "agent_handoff",
          name: "Transaction analysis",
          message: "Sherlock is investigating the transaction data.",
        }),
      ),
    );
    expect(await screen.findByText("Partial answer")).toBeInTheDocument();

    act(() => stream.close());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sherlock returned an invalid response",
    );
    expect(screen.queryByText("Partial answer")).not.toBeInTheDocument();
    expect(
      screen.getByText("Interrupted stream", { selector: ".user-message p" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Transaction analysis")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
        version: STORAGE_VERSION,
        messages: [
          { role: "user", content: "Interrupted stream" },
          {
            role: "assistant",
            content: "Sherlock returned an invalid response. Please try again.",
            outcome: "failed",
            activities: [{ name: "Transaction analysis", outcome: "failed" }],
          },
        ],
      }),
    );
  });

  it("renders GitHub-flavored Markdown tables in assistant messages", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(
        storedInvestigation([
          {
            role: "assistant",
            content:
              "| Metric | Value |\n|---|---|\n| **Average Fraud Transaction Value** | **$85.59 USD** |",
          },
        ]),
      ),
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
    localStorage.setItem(STORAGE_KEY, JSON.stringify(storedInvestigation([])));
    await user.click(screen.getByRole("button", { name: "New investigation" }));
    expect(
      screen.getByRole("heading", { name: /what would you like to uncover/i }),
    ).toBeInTheDocument();
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
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
    await user.type(screen.getByLabelText("Ask Sherlock"), "Successful question{enter}");

    expect(await screen.findByRole("heading", { name: "Failed question" })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).messages[0]).toEqual({
      role: "user",
      content: "Failed question",
    });
  });

  it("retains a malformed response as a safe failed turn for retry", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(
        storedInvestigation([{ role: "assistant", content: "Existing answer" }], {
          last_sql: "SELECT 1",
        }),
      ),
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
    expect(screen.getByText("Malformed turn", { selector: ".user-message p" })).toBeInTheDocument();
    expect(screen.getByText("Existing answer")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Ask Sherlock"), "Retry{enter}");
    await screen.findByText("I found a concentrated pattern.");
    const retryPayload = JSON.parse(
      String((vi.mocked(fetch).mock.calls[1][1] as RequestInit).body),
    );
    expect(retryPayload.working_state).toEqual({ last_sql: "SELECT 1" });
    expect(retryPayload.history).toEqual([
      { role: "assistant", content: "Existing answer" },
      { role: "user", content: "Malformed turn" },
      {
        role: "assistant",
        content: "Sherlock returned an invalid response. Please try again.",
        outcome: "failed",
      },
    ]);
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

  it("retains failed turns in bounded retry history", async () => {
    const messages = Array.from({ length: 20 }, (_, index) => ({
      id: `message-${index}`,
      role: (index % 2 ? "assistant" : "user") as "user" | "assistant",
      content: `Bounded ${index}`,
    }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(storedInvestigation(messages)));
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "Temporary failure" }, { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ ...chatResponse, artifacts: [] }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Ask Sherlock"), "Failed turn{enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Temporary failure");
    expect(screen.getByText("Failed turn", { selector: ".user-message p" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Ask Sherlock"), "Retry{enter}");
    await screen.findByText("I found a concentrated pattern.");
    const retryPayload = JSON.parse(
      String((vi.mocked(fetch).mock.calls[1][1] as RequestInit).body),
    );
    expect(retryPayload.history).toHaveLength(20);
    expect(retryPayload.history).toContainEqual({ role: "user", content: "Failed turn" });
    expect(retryPayload.history).toContainEqual(
      expect.objectContaining({ role: "assistant", outcome: "failed" }),
    );
  });

  it("restores a saved investigation and sends bounded plain history", async () => {
    const messages = Array.from({ length: 22 }, (_, index) => ({
      role: (index % 2 ? "assistant" : "user") as "user" | "assistant",
      content: `Message ${index}`,
    }));
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(storedInvestigation(messages, { last_sql: "SELECT 1" })),
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ ...chatResponse, artifacts: [] }),
    );
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByRole("heading", { name: "Message 2" })).toBeInTheDocument();
    expect(screen.queryByText("Message 0")).not.toBeInTheDocument();
    expect(screen.getByText("Message 21")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Ask Sherlock"), "Next{enter}");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const payload = JSON.parse(String((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body));
    expect(payload.history).toHaveLength(20);
    expect(payload.working_state.last_sql).toBe("SELECT 1");
  });

  it("sends persisted failed activity context with a later try-again turn", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: STORAGE_VERSION,
        conversation_id: SAVED_CONVERSATION_ID,
        messages: [
          { role: "user", content: "Explore further" },
          {
            role: "assistant",
            content: "Sherlock could not complete the request. Please try again.",
            outcome: "failed",
            activities: [
              {
                kind: "agent_handoff",
                name: "Transaction analysis",
                message: "Sherlock is investigating the transaction data.",
                result: "This activity could not be completed.",
                outcome: "failed",
              },
            ],
          },
        ],
        working_state: {},
      }),
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ ...chatResponse, artifacts: [] }),
    );
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByText("Transaction analysis")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Ask Sherlock"), "try again{enter}");

    const payload = JSON.parse(String((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body));
    expect(payload.history).toEqual([
      { role: "user", content: "Explore further" },
      {
        role: "assistant",
        content: "Sherlock could not complete the request. Please try again.",
        outcome: "failed",
        activities: [
          {
            kind: "agent_handoff",
            name: "Transaction analysis",
            message: "Sherlock is investigating the transaction data.",
            result: "This activity could not be completed.",
            outcome: "failed",
          },
        ],
      },
    ]);
  });
});
