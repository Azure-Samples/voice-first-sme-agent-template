import os

from fastapi import APIRouter

router = APIRouter()

# Provenance fields surfaced to operators. Three layers:
#   - templateVersion: upstream lineage (the VERSION the deployment forked
#     from). Coarse — does not reflect customer-local changes.
#   - gitSha + localChanges: source identity (the actual commit deployed,
#     plus whether the build was made from a clean worktree).
#   - imageDigest: byte identity (the immutable ACR digest this revision
#     pulls). Injected by Bicep at deploy time, not baked into the image.
#
# templateVersion, gitSha, and localChanges are baked into the image at
# build time via Dockerfile ARG/ENV. imageDigest is set at deploy time as
# a Container App env var (see .azure/modules/container-app.bicep).
TEMPLATE_VERSION = os.getenv("TEMPLATE_VERSION", "unknown")
GIT_SHA = os.getenv("GIT_SHA", "unknown")
LOCAL_CHANGES = os.getenv("LOCAL_CHANGES", "unknown")
IMAGE_DIGEST = os.getenv("IMAGE_DIGEST", "")
# Project-specific service name. Set by container-app.bicep in deployed
# environments and by setup-wizard local/deploy preflight. The "agent"
# fallback only applies to bare `uvicorn` runs outside those flows.
PROJECT_NAME = os.getenv("PROJECT_NAME", "agent")


@router.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": f"{PROJECT_NAME}-backend",
        "templateVersion": TEMPLATE_VERSION,
        "gitSha": GIT_SHA,
        "localChanges": LOCAL_CHANGES,
        "imageDigest": IMAGE_DIGEST,
    }
