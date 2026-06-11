# Deployment guide

This guide walks you through deploying your agent to Azure.

> **TL;DR**: clone the repo, run `./setup` to fill in `settings.json`, copy `.env.example` to `.env` and paste in your Voice Live API key, then run `./deploy`.

---

## What `./deploy` does

Fully static — no prompts. Reads `settings.json`, validates it, and:

1. Verifies the Azure CLI is installed and you are logged in (`az login`).
2. Switches to the configured subscription.
3. Creates the resource group if it doesn't exist.
4. Submits `infra/main.bicep` — a single deployment that creates Log Analytics, ACR, the user-assigned managed identity (with AcrPull on ACR), the Container Apps Environment, optionally Voice Live + AI Search, and the Container App itself.
5. Builds and pushes your container image via `az acr build`.
6. Registers the deployed app URL as a redirect URI on your Entra (Azure AD) app.
7. Writes the deployed URL back into `settings.json` under `deploy.appUrl`.

If anything in `settings.json` is missing, deploy fails fast and points you back to `./setup`.

---

## Prerequisites

1. **Azure subscription** with Contributor (or Owner) on the resource group you'll use.
2. **Azure CLI** — install from [learn.microsoft.com/cli/azure/install-azure-cli](https://learn.microsoft.com/cli/azure/install-azure-cli). Then `az login`.
3. **Node.js 18+**.
4. **Docker not required** — `az acr build` runs in the cloud.
5. **An Entra app registration** — see [app-registration.md](./app-registration.md). Set up once per tenant.
6. **Voice Live access** — your subscription needs the `gpt-realtime` model. If not enabled, request access at [aka.ms/oaiapply](https://aka.ms/oaiapply).
7. **Voice Live API key** — provide via a `.env` file (copy `.env.example`) or the `AZURE_VOICELIVE_API_KEY` env var. The key is **never written to `settings.json` or the image**.

---

## Setting the API key

```bash
# Easiest: copy the example file and paste your key in
cp .env.example .env
# then edit .env and set AZURE_VOICELIVE_API_KEY=<your-key>
```

Or set it in your shell:

```bash
# bash / zsh
export AZURE_VOICELIVE_API_KEY="<paste-key>"

# PowerShell
$env:AZURE_VOICELIVE_API_KEY = "<paste-key>"
```

Find or create the key in the Azure portal under your Voice Live (Cognitive Services) account → Keys and Endpoint. `.env` is gitignored.

---

## First-run deploy

```bash
./setup           # idempotent; fill in settings.json
./deploy          # bash / zsh
```

or on Windows PowerShell:

```powershell
.\setup.ps1
.\deploy.ps1
```

Expect the first deploy to take **5–10 minutes** (mostly the Voice Live model deployment, if you opted to create one).

When it finishes:

```
================================================================
  Deployed: https://<your-app>.<region>.azurecontainerapps.io
================================================================
```

---

## Re-deploying

After code or settings changes, just run `./deploy` again. The bicep is idempotent.

| Flag | Purpose |
|---|---|
| `--skip-build`     | Skip image build — useful when only infra/settings changed |
| `--skip-infra`     | Skip the bicep deploy — code-only iteration. Triggers a new revision so the freshly-built image rolls out |
| `--skip-preflight` | Skip the local app smoke-test (backend `import` + frontend typecheck). Don't use this unless you know the container will start. |

Before each deploy we run a **preflight**: import `app.main` locally with the
same env vars the container will see, and run `tsc --noEmit` against the
frontend. This catches the kind of crash that otherwise only shows up
10 minutes later as `CrashLoopBackOff` in Azure.

---

## Troubleshooting

**`AZURE_VOICELIVE_API_KEY env var is not set`**
Set it (see "Setting the API key" above). The script never reads or writes it from disk.

**`Missing settings.<key>`**
Run `./setup` to fill in the missing field. Setup is idempotent — every prompt pre-fills.

**`InvalidTemplateDeployment: Model gpt-realtime is not available in <region>`**
Voice Live is not in every region. Pick `eastus2` or `swedencentral` in setup.

**`AuthorizationFailed` on resource group create**
You need at least **Contributor** on the subscription. Ask your Azure admin.

**App starts but login fails with AADSTS error**
Check your app registration:
- Redirect URI matches `https://<your-app>.<region>.azurecontainerapps.io/` exactly (deploy registers this automatically; if it failed, the warning told you what URI to add)
- The app has the `User.Read` Microsoft Graph permission
- See [app-registration.md](./app-registration.md)

**I need to start over**
Delete the whole resource group:

```bash
az group delete --name <rg> --yes --no-wait
```

Then clear the `deploy` block in `settings.json` (set `appUrl` and `lastDeployedAt` to null) and re-run `./deploy`.

---

## What runs when

- **`./setup`** — edit `settings.json`. Idempotent. Run any time settings need to change.
- **`customize my agent`** *(inside a coding agent like Copilot or Claude Code)* — edits `system_prompt.txt`, knowledge base, etc.
- **`./deploy`** — pushes the current state of the repo to Azure.

The three are independent. You can deploy with default settings and customize later.
