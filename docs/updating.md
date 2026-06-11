# Receiving template updates

Your agent is a fork (or mirror clone) of the Agent Template. The platform team ships bug fixes, prompt improvements, and new features upstream. This page covers how to pull those changes in without disturbing your customizations.

## Update model

`./update` defaults to the latest `v*` release tag published upstream. That keeps customer updates on reviewed releases instead of whatever happens to be on `upstream/main`.

Use `--ref` only when you want an explicit target:

```bash
./update --ref v0.2.0          # specific release
./update --ref upstream/main   # unreleased work; use only when instructed
```

Run `./update --dry-run` first to inspect the upstream diff without merging it into your branch. Combine it with `--ref` when you want to inspect a specific release:

```bash
./update --dry-run --ref v0.2.0
```

If `./update` says no release tags exist, ask the upstream maintainers to publish an initial tag such as `v0.1.0`, or opt into `upstream/main` explicitly.

Before merging, the script prints the resolved target, the target `VERSION`, and a `CHANGELOG.md` diff so you can see what is about to change. In dry-run mode, it also prints the upstream file summary and full patch from your current merge base to the selected target, then exits before running `git merge`.

## Prompt update conflicts

At this point, `agents/<name>/system_prompt.txt` is customer-owned prose. If an upstream release includes a `prompt:` entry in `CHANGELOG.md`, your prompt customizations may conflict with upstream prompt improvements during `./update`.

Recommended handling when git reports a conflict in `agents/<name>/system_prompt.txt`:

1. Save or inspect your local prompt customizations.
2. Accept the upstream prompt as the new baseline.
3. Hand-merge your local customizations back on top.
4. Re-run your normal local/deploy validation.

Alternative: keep your local version and skip the upstream prompt change. That avoids prompt rework, but you miss any prompt-quality or safety improvements from the release.

Scan the `CHANGELOG.md` preview for `prompt:` entries before continuing. In a future prompt-template model, customer-owned prompt data will live separately from upstream prompt templates, reducing this conflict surface.

## What's "yours" vs. "upstream"

| Category | Owner | Files | Behavior on update |
|---|---|---|---|
| **Customer** | You | `settings.json`, `agents/<name>/*`, your `.env` | Safe to keep dirty. Pre-Phase-2 prompt prose can still conflict if upstream changes the same prompt file |
| **Platform** | Upstream | `backend/`, `frontend/`, `infra/`, `scripts/`, `Dockerfile`, `setup-wizard/`, `VERSION`, `CHANGELOG.md` | Updates come from upstream — local edits will conflict |

The `./update` script warns when you have local modifications anywhere outside customer-owned paths. The intent is that you customize via the customer-owned files only; if you find yourself patching platform files locally, flag it back to the platform team so the change can land upstream.

## What `./update` does NOT do

`./update` is a code-level merge — it pulls upstream source into your fork. It does **not** handle data-plane operations, Azure-side configuration, or customer-owned content. The following remain your responsibility (or require a separate step after `./update`):

- **AI Search index re-indexing.** Changes to chunking, embedding model, or index fields require re-running the indexer (or re-creating the index). `./deploy` after `./update` re-applies the schema setup; document re-ingestion is handled by Azure AI Search itself.
- **Cosmos DB schema migrations.** Container or partition-key changes need a manual data migration before redeploy. `./update` only moves code.
- **Entra/MSAL re-configuration.** Changes to authentication scopes, app-registration redirect URIs, or tenant configuration must be applied manually to your Entra app.
- **Custom-domain certificate renewal.** Managed-cert rotation is an Azure-side operation; `./update` doesn't touch it.
- **Customer-owned config and data** (`settings.json`, `agents/<name>/*`). You maintain these. `./update` is a source merge; it does not migrate or rewrite customer data. Before Phase 2, prompt prose can still produce normal git conflicts if upstream changed the same tracked prompt file.

After `./update`, run `./deploy` to apply the merged code changes. When a `CHANGELOG.md` entry mentions one of the categories above, plan the corresponding follow-up step.

## One-time setup: point at upstream

Pick whichever matches how you took the template:

### Option A — fork (GitHub UI)

If your GitHub Enterprise supports private forks, fork the template repo in the UI. Then clone your fork:

```bash
git clone <your-fork-url> my-agent
cd my-agent
git remote add upstream <upstream-template-url>
```

### Option B — mirror clone

Some enterprise tenants block forks across organizations. In that case:

```bash
git clone <upstream-template-url> my-agent
cd my-agent
git remote rename origin upstream
git remote add origin <your-private-repo-url>
git push -u origin main
```

Either way, `git remote -v` should show:

```
origin    <your repo>      (fetch + push)
upstream  <template repo>  (fetch + push)
```

## Pulling updates

```bash
./update                  # or: pwsh ./update.ps1 on Windows
```

To preview without merging:

```bash
./update --dry-run        # or: pwsh ./update.ps1 -DryRun on Windows
```

The script:

1. Verifies you're in a git repo with an `upstream` remote configured.
2. Warns if you have uncommitted edits outside customer-owned paths (`agents/*`), and aborts before a real merge.
3. Runs `git fetch upstream --tags`.
4. Resolves the latest `v*` release tag reachable from `upstream/main`.
5. Prints the resolved target, target `VERSION`, and `CHANGELOG.md` preview.
6. With `--dry-run`, prints the upstream diff preview and exits before merging.
7. Otherwise, merges the release tag into your current branch with an explicit merge message.

After it finishes, read `CHANGELOG.md` for what changed. If your `settings.json` is behind the new template version, the next `./setup` run will warn and migrate it (the wizard's `deepMerge` adds any new fields with defaults).

### Common scenarios

- **Conflicts in `backend/` or `infra/`** — you've edited platform files locally. Resolve manually, but consider sending the change upstream instead so you don't have to re-resolve every update.
- **Conflicts in `agents/<name>/system_prompt.txt`** — expected when both you and upstream changed prompt prose. See "Prompt update conflicts" above.
- **Conflicts in `settings.json`** — shouldn't happen; the file is gitignored in the template (see `.gitignore`).

## Choosing a target

By default, use the latest release:

```bash
./update
```

To pin to a specific release:

```bash
./update --ref v0.2.0
./update --dry-run --ref v0.2.0
```

To test unreleased upstream work:

```bash
./update --ref upstream/main
```

The `--ref` flag accepts a branch, tag, or commit. Prefer release tags unless the platform team asks you to test a specific unreleased fix.

## For platform maintainers: cutting a release

See [release-process.md](./release-process.md). Publish an initial Git tag such as `v0.1.0` before asking customers to use `./update`.

The image tag pushed by `./deploy` is `<version>-<sha>` and the running container surfaces full provenance via `GET /api/health`:

- `templateVersion` — upstream lineage (the VERSION this deployment forked from). Coarse — does not capture customer-local changes.
- `gitSha` + `localChanges` — source identity (the commit deployed, plus whether the worktree had uncommitted edits at build time).
- `imageDigest` — byte identity (the immutable ACR digest this Container App revision pulls).

Each field answers a different question. `templateVersion` is what you compare against the upstream `CHANGELOG.md` to decide if you need an update. `gitSha` + `localChanges` capture your fork's divergence. `imageDigest` is what rollback targets — see [`docs/rollback.md`](./rollback.md).
