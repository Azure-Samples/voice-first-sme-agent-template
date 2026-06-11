/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MSAL_CLIENT_ID: string;
  readonly VITE_MSAL_TENANT_ID: string;
  readonly VITE_MSAL_REDIRECT_URI: string;
  readonly VITE_MSAL_TRIBE_CLIENT_ID: string;
  readonly VITE_MSAL_TRIBE_TENANT_ID: string;
  readonly VITE_APP_TITLE: string;
  readonly VITE_APP_DESCRIPTION: string;
  readonly VITE_APP_DESCRIPTION_2: string;
  readonly VITE_APP_BUTTON_LABEL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
