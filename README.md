# Agent Template

A cloneable template for building and deploying a voice-enabled AI agent on Azure.

## Prerequisites

- **Node.js 18+** — runs the setup wizard and deploy script
- **Azure CLI** — authenticates and deploys to your Azure subscription ([install](https://learn.microsoft.com/cli/azure/install-azure-cli))
- **Python 3.11+** — renders the system prompt from your customization config. If unavailable, `./setup` and `./deploy` fall back to a static starter prompt so the agent still works, but prompt customization via `./render` and the customize-agent skill requires Python.

## Quick start

```bash
git clone <this-repo-url> my-agent
cd my-agent
./setup           # or: .\setup.cmd on Windows
```

The setup wizard writes your answers to `settings.json`. Re-run it any time to change settings — every prompt pre-fills with your current value.

After setup, customize your agent (optional) by opening this folder in your coding agent (Claude Code, GitHub Copilot, Cursor, Codex) and saying **"customize my agent"**.

## Preview locally (no Azure deploy)

```bash
./local           # or: .\local.cmd on Windows
```

Reads `settings.json`, runs the backend on `http://localhost:8000` and the frontend on `http://localhost:5173`. Auth is disabled locally so you can browse the UI without a working app reg. Voice still needs `AZURE_VOICELIVE_API_KEY` in `.env` and a deployed Voice Live endpoint in `settings.json` — without them the landing page renders but the "Talk" button won't connect.

When you're ready, deploy:

```bash
# 1. Set your Voice Live API key (we never store it in settings.json or the image)
cp .env.example .env
#    then paste your key into .env
#    (or set $env:AZURE_VOICELIVE_API_KEY in your shell instead)

# 2. Deploy
./deploy          # or: .\deploy.cmd on Windows
```

`deploy` is fully static — no prompts. It validates `settings.json`, deploys infrastructure, builds the image, and registers the deployed URL with your Entra app.

## How it's organized

```
settings.json              your config — single source of truth
setup, setup.ps1           edit settings.json
deploy, deploy.ps1         deploy to Azure
setup-wizard/              wizard implementation (Node)
infra/
  main.bicep               single deployment entry point
  modules/                 every available module — main.bicep picks via feature flags
frontend/                  React + Vite voice UI
backend/                   FastAPI backend (voice, auth, RAG)
agents/<your-agent>/       your agent's system prompt + knowledge base
Dockerfile                 multi-stage build (frontend + backend)
```

## Optional features

Enable in setup; deploy automatically picks up the right bicep modules:

- **RAG** — Azure AI Search for knowledge retrieval (backend wire-up TODO)
- **Custom domain** — point a domain at the deployed Container App
- **Custom voice** — Azure Personal Voice: enable in setup, paste your Speaker Profile ID into `settings.json` under `customVoice.profileId`, re-run `./deploy`. The backend reads it as `AZURE_PERSONAL_VOICE_PROFILE_ID` and the frontend switches to the `azure-personal` voice schema. Provisioning the Speech / AI Services account and onboarding the voice in Speech Studio is still a manual one-time step.

## Iterating

- **Code change only:** `./deploy --skip-infra`
- **Settings change:** re-run `./setup`, then `./deploy`
- **Force re-deploy of all infra:** `./deploy`

## Receiving template updates

This template ships with `VERSION`, `CHANGELOG.md`, and an `./update` script (`.\update.cmd` on Windows) that pulls the latest upstream release tag and warns before merging over local platform-file edits. Run `./update --dry-run` first to preview the upstream diff without merging. See [`docs/updating.md`](docs/updating.md) for the fork/clone setup and merge workflow.

`GET /api/health` reports full provenance for the running deployment: `templateVersion` (the upstream lineage your fork started from — coarse, not a unique fingerprint), `gitSha` + `localChanges` (your source identity including whether the build was made from a clean worktree), and `imageDigest` (the immutable bytes deployed). Image tags use `<version>-<sha>`.

To restore a previous deployed image, see [`docs/rollback.md`](docs/rollback.md).
