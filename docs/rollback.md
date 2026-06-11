# Rolling back a deployment

Agent Template deploys are pinned to container image digests when ACR digest resolution succeeds. That means you can roll back the running Container App to a previous image without rebuilding.

Rollback changes only the running container image. It does not roll back Azure resources, secrets, environment settings, data, `settings.json`, or any Bicep changes.

## Find the current pinned image

After a successful deploy, `settings.json` records the last image reference:

```json
{
  "deploy": {
    "lastImageRef": "<projectName>@sha256:<digest>",
    "lastImageTag": "0.1.0-abc1234"
  }
}
```

You can also inspect Container App revisions:

```bash
az containerapp revision list \
  --name <projectName> \
  --resource-group <resourceGroup> \
  --query "[].{name:name,image:properties.template.containers[0].image,active:properties.active}" \
  -o table
```

Copy the image reference for the revision you want to restore. Prefer a digest-pinned reference:

```text
<acrName>.azurecr.io/<projectName>@sha256:<digest>
```

## Roll back to a previous image

Run:

```bash
az containerapp update \
  --name <projectName> \
  --resource-group <resourceGroup> \
  --image <acrName>.azurecr.io/<projectName>@sha256:<previousDigest> \
  --revision-suffix rollback-$(date +%s)
```

On PowerShell:

```powershell
$suffix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
az containerapp update `
  --name <projectName> `
  --resource-group <resourceGroup> `
  --image <acrName>.azurecr.io/<projectName>@sha256:<previousDigest> `
  --revision-suffix "rollback-$suffix"
```

## Verify

Check the active revision and health endpoint:

```bash
az containerapp revision list \
  --name <projectName> \
  --resource-group <resourceGroup> \
  --query "[?properties.active].{name:name,image:properties.template.containers[0].image}" \
  -o table

curl https://<appUrl>/api/health
```

The health response should show the `templateVersion` and `gitSha` baked into the restored image.

Note: `settings.json.deploy.lastImageRef` reflects the most recent `./deploy`, not the result of a manual rollback. After a rollback, the Container App revision list is the source of truth for what's actually running.

## If the previous deploy used a tag instead of a digest

If digest resolution failed during deploy, `settings.json` may contain an image tag such as:

```text
<projectName>:0.1.0-abc1234
```

You can still update the Container App to that tag, but it is not an immutable rollback. Tags can be overwritten by later builds. Use digest-pinned image references whenever possible.
