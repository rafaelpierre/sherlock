import { FormEvent, KeyboardEvent, useEffect, useRef, useState, type Ref } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sendChat } from "./api";
import { ArtifactView } from "./Artifacts";
import { PaperclipIcon, PlusIcon, SendIcon, SparkIcon } from "./Icons";
import { loadInvestigation, newInvestigation, saveInvestigation } from "./store";
import type { Investigation, StreamActivity, TranscriptMessage } from "./types";
import "./styles.css";

const suggestions = [
  "Which card type has the highest fraud rate?",
  "Create a candidate rule for high-value debit transactions",
  "Show fraud value by merchant category",
];
const MAX_MESSAGE_LENGTH = 2_000;

function Composer({
  value,
  setValue,
  submit,
  disabled,
  centered = false,
}: {
  value: string;
  setValue: (value: string) => void;
  submit: () => void;
  disabled: boolean;
  centered?: boolean;
}) {
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }
  return (
    <form
      className={`composer ${centered ? "composer-centered" : ""}`}
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        submit();
      }}
    >
      <label className="sr-only" htmlFor="question">
        Ask Sherlock
      </label>
      <textarea
        id="question"
        rows={centered ? 5 : 1}
        placeholder={
          centered
            ? "Describe the fraud pattern you want to investigate…"
            : "Ask Sherlock anything about your fraud data…"
        }
        value={value}
        maxLength={MAX_MESSAGE_LENGTH}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={keyDown}
      />
      <div className="composer-actions">
        <button
          className="attach"
          type="button"
          aria-label="Attach context"
          title="Attachments coming soon"
        >
          <PaperclipIcon />
        </button>
        <button
          className="send"
          type="submit"
          aria-label="Send message"
          disabled={disabled || !value.trim()}
        >
          <SendIcon />
        </button>
      </div>
    </form>
  );
}

function Activity({ message }: { message: string }) {
  return (
    <div className="activity" role="status">
      <div className="agent-card">
        <div>
          <span className="agent-icon">
            <SparkIcon />
          </span>
          <div>
            <strong>Data Analyst</strong>
            <p>Senior SQL data analyst · investigates data and validates results</p>
          </div>
        </div>
        <span className="version">◆ v1.0.0</span>
        <small>Data analysis</small>
      </div>
      <div className="activity-prompt">{message}</div>
      <div className="analyzing">
        <span>
          <SparkIcon />
        </span>
        Analyzing<span className="dots">···</span>
      </div>
    </div>
  );
}

function StreamActivityView({ activity }: { activity: StreamActivity }) {
  const [open, setOpen] = useState(!activity.result);
  const completed = Boolean(activity.result);

  useEffect(() => {
    if (completed) setOpen(false);
  }, [completed]);

  return (
    <details
      className="stream-activity"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>{activity.kind === "agent_handoff" ? "Agent handoff" : "Tool call"}</span>
        <strong>{activity.name}</strong>
        <small>{completed ? "Complete" : "Running"}</small>
      </summary>
      <p>{activity.message}</p>
      {activity.result && <div className="stream-result">{activity.result}</div>}
    </details>
  );
}

function Message({
  message,
  evidenceRef,
}: {
  message: TranscriptMessage;
  evidenceRef?: Ref<HTMLDivElement>;
}) {
  if (message.role === "user")
    return (
      <article className="user-message">
        <span className="message-label">You</span>
        <p>{message.content}</p>
      </article>
    );
  return (
    <article
      aria-live={message.id?.startsWith("stream-") ? "polite" : undefined}
      className="assistant-message"
    >
      <div className="assistant-mark">
        <SparkIcon />
      </div>
      <div className="assistant-content">
        <span className="message-label">Sherlock</span>
        {message.activities?.map((activity) => (
          <StreamActivityView activity={activity} key={activity.id} />
        ))}
        {message.artifacts && message.artifacts.length > 0 && (
          <div className="artifacts" ref={evidenceRef}>
            {message.artifacts.map((artifact, index) => (
              <ArtifactView artifact={artifact} key={`${artifact.type}-${index}`} />
            ))}
          </div>
        )}
        {message.content && (
          <div className="prose">
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                table: ({ children }) => (
                  <div className="table-scroll markdown-table">
                    <table>{children}</table>
                  </div>
                ),
              }}
            >
              {message.content}
            </Markdown>
          </div>
        )}
      </div>
    </article>
  );
}

export default function App() {
  const [investigation, setInvestigation] = useState<Investigation>(() => loadInvestigation());
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [streamStarted, setStreamStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const evidenceRef = useRef<HTMLDivElement>(null);
  const revealCompletedEvidence = useRef(false);
  const requestGeneration = useRef(0);
  const currentRequest = useRef<AbortController | null>(null);
  const empty = investigation.messages.length === 0;

  useEffect(() => {
    if (!pending) saveInvestigation(investigation);
  }, [investigation, pending]);
  useEffect(() => {
    if (revealCompletedEvidence.current) {
      if (!pending) {
        evidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        revealCompletedEvidence.current = false;
      }
      return;
    }
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [investigation.messages, pending]);

  async function submit(value = input) {
    const message = value.trim();
    if (!message || pending) return;
    if (message.length > MAX_MESSAGE_LENGTH) {
      setError(`Questions must be ${MAX_MESSAGE_LENGTH.toLocaleString()} characters or fewer.`);
      return;
    }
    const requestId = ++requestGeneration.current;
    const draftId = `stream-${requestId}`;
    const controller = new AbortController();
    currentRequest.current = controller;
    const history = investigation.messages.map(({ role, content }) => ({ role, content }));
    setInput("");
    setError(null);
    setPending(true);
    setStreamStarted(false);
    setInvestigation((current) => ({
      ...current,
      title: current.title ?? message,
      messages: [
        ...current.messages,
        { id: crypto.randomUUID(), role: "user" as const, content: message },
      ].slice(-20),
    }));
    try {
      const response = await sendChat(
        investigation.conversationId,
        message,
        history,
        investigation.workingState,
        {
          onEvent: (event) => {
            if (requestId !== requestGeneration.current) return;
            setStreamStarted(true);
            setInvestigation((current) => {
              const draftIndex = current.messages.findIndex(({ id }) => id === draftId);
              const draft: TranscriptMessage =
                draftIndex === -1
                  ? { id: draftId, role: "assistant", content: "", activities: [] }
                  : current.messages[draftIndex];
              let updated = draft;
              if (event.type === "text_delta") {
                updated = { ...draft, content: draft.content + event.delta };
              } else if (event.type === "tool_call") {
                updated = {
                  ...draft,
                  activities: [...(draft.activities ?? []), event],
                };
              } else {
                updated = {
                  ...draft,
                  activities: (draft.activities ?? []).map((activity) =>
                    activity.id === event.id ? { ...activity, result: event.message } : activity,
                  ),
                };
              }
              const messages = [...current.messages];
              if (draftIndex === -1) messages.push(updated);
              else messages[draftIndex] = updated;
              return { ...current, messages: messages.slice(-20) };
            });
          },
        },
        controller.signal,
      );
      if (requestId !== requestGeneration.current) return;
      revealCompletedEvidence.current = response.artifacts.length > 0;
      setInvestigation((current) => {
        const draftIndex = current.messages.findIndex(({ id }) => id === draftId);
        const completed: TranscriptMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.message,
          artifacts: response.artifacts,
          intent: response.metadata.intent,
          activities: draftIndex === -1 ? undefined : current.messages[draftIndex].activities,
        };
        const messages = [...current.messages];
        if (draftIndex === -1) messages.push(completed);
        else messages[draftIndex] = completed;
        return {
          ...current,
          workingState: response.working_state,
          messages: messages.slice(-20),
        };
      });
    } catch (reason) {
      if (requestId !== requestGeneration.current) return;
      setInvestigation((current) => ({
        ...current,
        title: investigation.title,
        messages: investigation.messages,
      }));
      setError(
        reason instanceof Error ? reason.message : "Sherlock encountered an unexpected error.",
      );
    } finally {
      if (requestId === requestGeneration.current) {
        currentRequest.current = null;
        setPending(false);
      }
    }
  }

  function reset() {
    currentRequest.current?.abort();
    currentRequest.current = null;
    requestGeneration.current += 1;
    setInvestigation(newInvestigation());
    setInput("");
    setError(null);
    setPending(false);
    setStreamStarted(false);
    revealCompletedEvidence.current = false;
  }

  return (
    <div className="shell">
      <header>
        <a
          className="brand"
          href="/"
          onClick={(event) => {
            event.preventDefault();
            reset();
          }}
        >
          <span>
            <SparkIcon />
          </span>
          <strong>Sherlock</strong>
          <small>Fraud intelligence</small>
        </a>
        <button className="new-button" type="button" onClick={reset}>
          <PlusIcon />
          New investigation
        </button>
      </header>
      <main className={empty ? "empty" : "conversation"}>
        {empty ? (
          <section className="welcome">
            <div className="eyebrow">
              <SparkIcon />
              Investigate with confidence
            </div>
            <h1>
              What would you like
              <br />
              to <em>uncover?</em>
            </h1>
            <p>
              Ask a question about your fraud data, explore suspicious patterns, or turn an insight
              into a rule.
            </p>
            <Composer
              value={input}
              setValue={setInput}
              submit={() => void submit()}
              disabled={pending}
              centered
            />
            {error && (
              <div className="input-error" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => setError(null)}>
                  Dismiss
                </button>
              </div>
            )}
            <div className="suggestions">
              {suggestions.map((suggestion) => (
                <button type="button" key={suggestion} onClick={() => void submit(suggestion)}>
                  {suggestion}
                  <span>↗</span>
                </button>
              ))}
            </div>
          </section>
        ) : (
          <>
            <div className="conversation-title">
              <span>Investigation</span>
              <h1>{investigation.title}</h1>
            </div>
            <section className="transcript">
              {investigation.messages.map((message, index) => (
                <Message
                  message={message}
                  key={message.id}
                  evidenceRef={
                    index === investigation.messages.length - 1 ? evidenceRef : undefined
                  }
                />
              ))}
              {pending && !streamStarted && (
                <Activity message={investigation.messages.at(-1)?.content ?? input} />
              )}
              {error && (
                <div className="error" role="alert">
                  <strong>Something interrupted the investigation</strong>
                  <span>{error}</span>
                  <button type="button" onClick={() => setError(null)}>
                    Dismiss
                  </button>
                </div>
              )}
              <div ref={endRef} />
            </section>
            <div className="sticky-composer">
              <Composer
                value={input}
                setValue={setInput}
                submit={() => void submit()}
                disabled={pending}
              />
              <p>Candidate rules are investigation hypotheses, not production decisions.</p>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
