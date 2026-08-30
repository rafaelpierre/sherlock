import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { sendChat } from "./api";
import { ArtifactView } from "./Artifacts";
import { PaperclipIcon, PlusIcon, SendIcon, SparkIcon } from "./Icons";
import { loadInvestigation, newInvestigation, saveInvestigation } from "./store";
import type { Investigation, TranscriptMessage } from "./types";
import "./styles.css";

const suggestions = [
  "Which card type has the highest fraud rate?",
  "Create a candidate rule for high-value debit transactions",
  "Show fraud value by merchant category",
];

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

function Message({ message }: { message: TranscriptMessage }) {
  if (message.role === "user")
    return (
      <article className="user-message">
        <span className="message-label">You</span>
        <p>{message.content}</p>
      </article>
    );
  return (
    <article className="assistant-message">
      <div className="assistant-mark">
        <SparkIcon />
      </div>
      <div className="assistant-content">
        <span className="message-label">Sherlock</span>
        <div className="prose">
          <Markdown>{message.content}</Markdown>
        </div>
        {message.artifacts?.map((artifact, index) => (
          <ArtifactView artifact={artifact} key={`${artifact.type}-${index}`} />
        ))}
      </div>
    </article>
  );
}

export default function App() {
  const [investigation, setInvestigation] = useState<Investigation>(() => loadInvestigation());
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const empty = investigation.messages.length === 0;

  useEffect(() => {
    saveInvestigation(investigation);
  }, [investigation]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [investigation.messages, pending]);

  async function submit(value = input) {
    const message = value.trim();
    if (!message || pending) return;
    const history = investigation.messages.map(({ role, content }) => ({ role, content }));
    setInput("");
    setError(null);
    setPending(true);
    setInvestigation((current) => ({
      ...current,
      messages: [...current.messages, { role: "user", content: message }],
    }));
    try {
      const response = await sendChat(
        investigation.conversationId,
        message,
        history,
        investigation.workingState,
      );
      setInvestigation((current) => ({
        ...current,
        workingState: response.working_state,
        messages: [
          ...current.messages,
          {
            role: "assistant" as const,
            content: response.message,
            artifacts: response.artifacts,
            intent: response.metadata.intent,
          },
        ].slice(-20),
      }));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Sherlock encountered an unexpected error.",
      );
    } finally {
      setPending(false);
    }
  }

  function reset() {
    setInvestigation(newInvestigation());
    setInput("");
    setError(null);
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
              <h1>{investigation.messages.find((message) => message.role === "user")?.content}</h1>
            </div>
            <section className="transcript">
              {investigation.messages.map((message, index) => (
                <Message message={message} key={index} />
              ))}
              {pending && <Activity message={investigation.messages.at(-1)?.content ?? input} />}
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
