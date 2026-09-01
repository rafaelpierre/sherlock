// Replaced at runtime by nginx in deployed environments. Empty values disable
// Cognito for the explicitly local, unauthenticated development workflow.
window.__SHERLOCK_AUTH_CONFIG__ = {
  issuer: "",
  clientId: "",
  hostedUiDomain: "",
};
