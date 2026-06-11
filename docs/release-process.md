# Release process

This process keeps `VERSION`, `CHANGELOG.md`, GitHub Releases, and deployed images aligned.

## When to bump `VERSION`

Bump `VERSION` for any merge to `main` that changes customer-visible behavior in one of these categories:

- **infra**: Bicep, ACR, Container Apps, Voice Live, Cosmos, AI Search
- **app**: backend or frontend behavior
- **prompt**: system prompt, persona behavior, customization model
- **schema**: `settings.json` or `customization.json` shape
- **dependencies**: Python or Node package updates

Doc-only and test-only changes do not require a version bump unless they change the customer update or deployment process.

## Semver rules

- **Major**: breaking change to `settings.json`, `customization.json`, required Bicep params, backend API shape, or removal of a feature flag.
- **Minor**: additive optional features, additive Bicep modules, new feature flags, new schema fields with defaults, new starter packs, or additive backend routes.
- **Patch**: bug fixes, prompt-quality tweaks that preserve persona shape, dependency bumps, and compatible infra parameter tweaks.

## Builds, releases, and rollback

Every deploy builds an image tagged `<version>-<sha>`, where `version` comes from `VERSION` and `sha` is the current Git commit. The deploy then resolves that tag to an immutable image digest and pins the Container App revision to `<acr>.azurecr.io/<repo>@sha256:<digest>`.

Git release tags are the customer update boundary. Customers should receive template updates through `v*` Git tags and GitHub Releases, not by tracking `upstream/main`. The container image tag remains commit-specific; release builds do not currently publish a bare `<version>` image tag. Rollback should use the pinned image digest recorded in `settings.json` or visible on the Container App revision.

## Runbook

1. Update `VERSION` using semver.
2. Add a matching `CHANGELOG.md` entry under `## [<version>] - <YYYY-MM-DD>`.
3. Merge the release change to `main`.
4. Create an annotated Git tag:
   ```bash
   git tag -a v<version> -m "Release v<version>"
   git push origin v<version>
   ```
5. Create a GitHub Release from the tag:
   ```bash
   gh release create v<version> --title "v<version>"
   ```
   Paste the matching CHANGELOG section into the release notes.
6. Deploy and verify:
   ```bash
   ./deploy
   curl https://<appUrl>/api/health
   ```

The health response should show the released `templateVersion` and the build `gitSha`.

## Verify customer update path

Run this from a fresh customer fork or mirror clone, not from the upstream maintainer checkout:

```bash
git remote add upstream <upstream-template-url>  # if not already configured
git fetch upstream --tags
./update --dry-run --ref v<version>
./update --ref v<version>
```

The dry run should resolve the requested tag, print the target `VERSION`, show the `CHANGELOG.md` diff, and print the upstream patch without merging. The follow-up run should merge the release into the customer branch.

## Future pipeline hardening

Add Bicep validation to CI before deploy when the release timeline can absorb pipeline hardening risk:

- Static syntax/type validation: `az bicep build --file .azure/main.bicep`
- Stronger environment validation: `az deployment group validate` with placeholder secret values and the same params used by deploy

`az deployment group validate` is valuable but environment-sensitive: it can fail because of Azure permissions, provider registration, existing resource assumptions, or cross-resource-group access. Add it deliberately as a pipeline hardening item after the current release path is stable.
