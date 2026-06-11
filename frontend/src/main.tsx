import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PublicClientApplication } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { FluentProvider } from "@fluentui/react-components";
import { msalConfig } from "./config/auth";
import { agentJTheme } from "./config/theme";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "./index.css";
import App from "./App";

// Set page title from env var (Vite doesn't interpolate env vars in index.html)
document.title = import.meta.env.VITE_APP_TITLE || "Voice Agent";

const msalInstance = new PublicClientApplication(msalConfig);

function renderApp() {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <FluentProvider theme={agentJTheme}>
        <MsalProvider instance={msalInstance}>
          <App />
        </MsalProvider>
      </FluentProvider>
    </StrictMode>,
  );
}

// Ensure MSAL processes any redirect response before rendering. If the
// redirect-handling promise rejects (most often `no_token_request_cache_error`
// — caused by a stale OAuth hash in the URL after a deploy rolled and wiped
// the in-flight request entry), don't leave the user staring at a white
// screen. Clear the auth fragment from the URL and render anyway; the user
// will land unauthenticated and the normal login flow will take over.
msalInstance.initialize().then(() => {
  msalInstance.handleRedirectPromise()
    .then((response) => {
      if (response) {
        msalInstance.setActiveAccount(response.account);
      } else {
        const accounts = msalInstance.getAllAccounts();
        if (accounts.length > 0) {
          msalInstance.setActiveAccount(accounts[0]);
        }
      }
    })
    .catch((err) => {
      console.warn("[MSAL] handleRedirectPromise failed; continuing unauthenticated", err);
      if (window.location.hash && /[?#].*\b(code|state|error)=/.test(window.location.hash + window.location.search)) {
        const cleanUrl = window.location.pathname + window.location.search.replace(/[?&](code|state|session_state|error|error_description)=[^&]*/g, "").replace(/^\?$/, "");
        window.history.replaceState({}, document.title, cleanUrl);
      }
    })
    .finally(() => {
      renderApp();
    });
});
