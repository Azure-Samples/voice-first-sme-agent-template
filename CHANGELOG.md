# Changelog

All notable changes to the Agent P template are recorded here.

This project follows [Semantic Versioning](https://semver.org/). The current template version is in [`VERSION`](VERSION) and is surfaced at runtime via `GET /api/health` (`templateVersion` field) and on container images as the tag `<version>-<sha>`. See [`docs/release-process.md`](docs/release-process.md) for release rules and runbook.

Changes are grouped by area:

- **infra**: Bicep, ACR, Container Apps, Voice Live, Cosmos, AI Search
- **app**: backend (FastAPI) or frontend (React) code
- **prompt**: system prompt / agent persona / customization model
- **schema**: `settings.json` or `customization.json` shape
- **dependencies**: Python/Node package updates

## [0.1.0] - 2026-05-14

Initial tagged release.

- **infra**: Versioned container images (`<version>-<sha>`), Container App revisions pinned to the resolved image digest, Voice Live realtime deployment with explicit `modelVersion`.
- **app**: `GET /api/health` reports `templateVersion`, `gitSha`, `localChanges`, `imageDigest`, and a project-specific `service` name. Voice/TTS audio pre-generation is non-fatal at startup. Voice Live WebSocket failures surface to the frontend as an `error` state and log secret-redacted backend diagnostics.
- **schema**: `settings.json.version` is a semver string; `./setup` migrates legacy integer values.
- **docs**: `docs/release-process.md` and `docs/updating.md` cover the release runbook and `./update` (defaults to the latest upstream `v*` tag; `--ref` / `--dry-run` available).
