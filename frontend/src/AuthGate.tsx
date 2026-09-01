import { useEffect, useState, type ReactNode } from "react";
import { initializeAuthentication } from "./auth";

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [error, setError] = useState("Sign-in could not be completed.");

  useEffect(() => {
    void initializeAuthentication()
      .then(() => setState("ready"))
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Sign-in could not be completed.");
        setState("failed");
      });
  }, []);

  if (state === "failed") return <main role="alert">{error}</main>;
  return state === "ready" ? <>{children}</> : <main aria-live="polite">Signing you in…</main>;
}
