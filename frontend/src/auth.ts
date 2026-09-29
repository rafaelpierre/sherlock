declare global {
  interface Window {
    __SHERLOCK_AUTH_CONFIG__?: {
      issuer?: string;
      clientId?: string;
      hostedUiDomain?: string;
    };
  }
}

type AuthConfig = Required<NonNullable<Window["__SHERLOCK_AUTH_CONFIG__"]>>;

let accessToken: string | null = null;

function config(): AuthConfig | null {
  const candidate = window.__SHERLOCK_AUTH_CONFIG__;
  if (!candidate?.issuer || !candidate.clientId || !candidate.hostedUiDomain) return null;
  return {
    issuer: candidate.issuer.replace(/\/$/, ""),
    clientId: candidate.clientId,
    hostedUiDomain: candidate.hostedUiDomain.replace(/\/$/, ""),
  };
}

function callbackUrl(): string {
  return `${window.location.origin}/`;
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

async function redirectToLogin(auth: AuthConfig): Promise<never> {
  const state = crypto.randomUUID();
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(32)));
  sessionStorage.setItem("sherlock.auth.state", state);
  sessionStorage.setItem("sherlock.auth.verifier", verifier);
  const url = new URL(`${auth.hostedUiDomain}/oauth2/authorize`);
  url.search = new URLSearchParams({
    client_id: auth.clientId,
    code_challenge: await pkceChallenge(verifier),
    code_challenge_method: "S256",
    redirect_uri: callbackUrl(),
    response_type: "code",
    scope: "openid",
    state,
  }).toString();
  window.location.assign(url);
  throw new Error("Redirecting to Cognito");
}

async function completeLogin(auth: AuthConfig, code: string): Promise<void> {
  const expectedState = sessionStorage.getItem("sherlock.auth.state");
  const verifier = sessionStorage.getItem("sherlock.auth.verifier");
  if (
    !expectedState ||
    !verifier ||
    new URLSearchParams(window.location.search).get("state") !== expectedState
  ) {
    throw new Error("The sign-in response could not be verified. Please try again.");
  }
  const response = await fetch(`${auth.hostedUiDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: auth.clientId,
      code,
      code_verifier: verifier,
      grant_type: "authorization_code",
      redirect_uri: callbackUrl(),
    }),
  });
  if (!response.ok) throw new Error("Sign-in could not be completed. Please try again.");
  const body = (await response.json()) as { access_token?: unknown };
  if (typeof body.access_token !== "string" || !body.access_token) {
    throw new Error("Sign-in did not return an access token. Please try again.");
  }
  accessToken = body.access_token;
  sessionStorage.removeItem("sherlock.auth.state");
  sessionStorage.removeItem("sherlock.auth.verifier");
  window.history.replaceState({}, document.title, window.location.pathname);
}

export function currentAccessToken(): string | null {
  return accessToken;
}

export function restartLogin(): void {
  const auth = config();
  if (auth) void redirectToLogin(auth);
}

export async function initializeAuthentication(): Promise<void> {
  const auth = config();
  if (!auth) return;
  const code = new URLSearchParams(window.location.search).get("code");
  if (!code) {
    await redirectToLogin(auth);
  } else {
    await completeLogin(auth, code);
  }
}
