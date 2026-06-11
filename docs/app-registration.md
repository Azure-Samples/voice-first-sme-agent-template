# Azure AD app registration

Your agent authenticates users with Microsoft Entra ID (Azure AD). This requires a one-time **app registration** in your tenant. Expect ~5 minutes.

> You only do this once per tenant. After that, the Client ID and Tenant ID go into `settings.json` and are reused on every deploy.

---

## Prerequisites

- You are signed in to the [Azure portal](https://portal.azure.com) with a user who can create app registrations in your tenant. (Most enterprise tenants restrict this to admins — ask yours if you hit a wall.)
- You know the **custom domain or Container Apps URL** your agent will run on. If you haven't deployed yet, you can **put a placeholder now and update it after the first deploy** — the script will print the URL and you can come back and add it.

---

## Step 1 — Create the app registration

1. Go to [**Microsoft Entra ID** → **App registrations** → **New registration**](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. **Name**: something like `My Agent (dev)`. The user never sees this.
3. **Supported account types**: pick `Accounts in this organizational directory only` unless you have a multi-tenant reason not to.
4. **Redirect URI**: pick **Single-page application (SPA)** and enter:
   ```
   https://<your-app-url>/auth/callback
   ```
   If you don't know the URL yet, put `http://localhost:5173/auth/callback` for now and add the real one after your first `./deploy`.
5. Click **Register**.

---

## Step 2 — Copy the IDs

On the app's **Overview** page, copy:

| What the portal calls it | What the deploy script calls it |
|---|---|
| **Application (client) ID** | MSAL Client ID |
| **Directory (tenant) ID** | MSAL Tenant ID |

These are the two GUIDs `./deploy` will prompt you for.

---

## Step 3 — Add the Microsoft Graph permission

1. **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**.
2. Search for `User.Read`, check it, click **Add permissions**.
3. If your tenant requires admin consent, click **Grant admin consent**.

---

## Step 4 — Add redirect URIs after deploy

After your first `./deploy` prints the app URL:

1. Go back to your app registration → **Authentication**.
2. Under **Single-page application**, add:
   ```
   https://<your-app-url>/auth/callback
   ```
3. Save.

If you have a custom domain, add a redirect for that too.

---

## Step 5 — Logout URL (optional but recommended)

On the same **Authentication** page, under **Front-channel logout URL**:

```
https://<your-app-url>/auth/logout
```

---

## Troubleshooting

**`AADSTS50011: The redirect URI specified in the request does not match`**
The URL in your app registration must exactly match what the frontend sent, including protocol and path. Copy-paste it — don't retype.

**`AADSTS700016: Application with identifier ... was not found`**
Client ID in `settings.json` is wrong or the app reg was deleted.

**`AADSTS90009: Application ... is requesting a token for itself`**
Client ID and Tenant ID are swapped.

**"Users outside my org can't sign in"**
You picked "Accounts in this organizational directory only" in step 1. Change to multi-tenant under **Authentication** if you need external users.
