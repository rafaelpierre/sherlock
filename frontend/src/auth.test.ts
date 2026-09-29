import { currentAccessToken, initializeAuthentication } from "./auth";

describe("Cognito authentication", () => {
  it("exchanges a verified authorization-code callback and retains only the access token in memory", async () => {
    window.__SHERLOCK_AUTH_CONFIG__ = {
      issuer: "https://cognito-idp.eu-west-2.amazonaws.com/pool",
      clientId: "client-id",
      hostedUiDomain: "https://sherlock.auth.eu-west-2.amazoncognito.com",
    };
    sessionStorage.setItem("sherlock.auth.state", "expected-state");
    sessionStorage.setItem("sherlock.auth.verifier", "verifier");
    window.history.replaceState({}, "", "/?code=code-value&state=expected-state");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ access_token: "access-token", refresh_token: "ignored" }), {
        status: 200,
      }),
    );

    await initializeAuthentication();

    expect(fetch).toHaveBeenCalledWith(
      "https://sherlock.auth.eu-west-2.amazoncognito.com/oauth2/token",
      expect.objectContaining({ method: "POST" }),
    );
    expect(currentAccessToken()).toBe("access-token");
    expect(sessionStorage.getItem("sherlock.auth.state")).toBeNull();
    expect(sessionStorage.getItem("sherlock.auth.verifier")).toBeNull();
    expect(window.location.search).toBe("");
  });
});
