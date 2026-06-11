import type { Configuration } from "@azure/msal-browser";
import { LogLevel } from "@azure/msal-browser";

// Three login surfaces — chosen by URL path:
//   /            → Microsoft app reg (single-tenant, microsoft.com only)
//   /tribe       → Tribe app reg, authority = Tribe tenant (Tribe employees)
//   /login       → Tribe app reg, authority = /organizations (any partner tenant)
//
// /tribe and /login share the SAME app registration (which must be flagged
// AzureADMultipleOrgs in Entra). The only difference is the authority: /tribe
// uses the Tribe-tenant GUID so Tribe employees land directly on their tenant
// sign-in, while /login uses /organizations so partners are routed to their
// own home tenant. The backend (partners.json + auth.py) gates which external
// partners are actually allowed in once their token reaches the server.
const path = window.location.pathname;
const isTribePath = path.startsWith("/tribe");
const isLoginPath = path.startsWith("/login");
const isPartnerLogin = isTribePath || isLoginPath;

const clientId = isPartnerLogin
  ? import.meta.env.VITE_MSAL_TRIBE_CLIENT_ID
  : import.meta.env.VITE_MSAL_CLIENT_ID;

const tribeTenantId = import.meta.env.VITE_MSAL_TRIBE_TENANT_ID;
const microsoftTenantId = import.meta.env.VITE_MSAL_TENANT_ID;

const authority = isLoginPath
  ? "https://login.microsoftonline.com/organizations"
  : `https://login.microsoftonline.com/${isTribePath ? tribeTenantId : microsoftTenantId}`;

const redirectUri = isLoginPath
  ? "/login"
  : isTribePath
  ? "/tribe"
  : (import.meta.env.VITE_MSAL_REDIRECT_URI || "/");

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority,
    redirectUri,
    // /organizations is not a recognized tenant authority by MSAL's default
    // validator; it's the multi-tenant marker. Only set knownAuthorities for
    // that surface — DO NOT set it (even to undefined) for /, otherwise the
    // spread merge in MSAL's buildConfiguration overwrites the default `[]`
    // with `undefined` and the first .filter() call inside Authority
    // throws a TypeError that gets rewrapped as endpoints_resolution_error.
    ...(isLoginPath ? { knownAuthorities: ["login.microsoftonline.com"] } : {}),
  },
  cache: {
    cacheLocation: "sessionStorage",
  },
  system: {
    loggerOptions: {
      // Warning is enough for production. Bump to `LogLevel.Verbose` locally
      // when debugging MSAL — the obfuscated 6-char codes can be looked up
      // in node_modules/@azure/msal-common/dist-browser/**/*.mjs.
      logLevel: LogLevel.Warning,
      piiLoggingEnabled: false,
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        // eslint-disable-next-line no-console
        const tag = "[MSAL]";
        if (level === LogLevel.Error)        console.error(tag, message);
        else if (level === LogLevel.Warning) console.warn(tag, message);
        else if (!import.meta.env.DEV)       return;
        else if (level === LogLevel.Info)    console.info(tag, message);
        else                                 console.log(tag, message);
      },
    },
  },
};

export const loginRequest = {
  scopes: [`${clientId}/.default`],
};
