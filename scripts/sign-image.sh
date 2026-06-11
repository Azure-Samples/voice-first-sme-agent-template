#!/usr/bin/env bash
# sign-image.sh — sign a container image in ACR with Notation + Azure Key Vault.
#
# Closes the SDL "Container image security" finding by producing a COSE
# signature on the image manifest, stored as an OCI artifact alongside
# the image in ACR (per the Notary Project / Notation v2 spec).
#
# Required environment variables:
#   ACR_NAME                — registry short name (e.g. "__ACR_NAME__")
#   IMAGE_REF               — full image reference including tag
#                             (e.g. "__ACR_NAME__.azurecr.io/__PROJECT_NAME__:12345")
#   SIGNING_KEY_VAULT_NAME  — Azure Key Vault name holding the signing key
#   SIGNING_KEY_NAME        — key name in the vault
#
# Optional environment variables:
#   SIGNING_KEY_VERSION     — key version (default: latest)
#   NOTATION_PLUGIN_CONFIG  — plugin config k=v (e.g. "self_signed=true"
#                             when the AKV cert is self-signed instead of
#                             CA-issued)
#   NOTATION_VERSION        — notation CLI version (default: 1.2.0)
#   NOTATION_AKV_VERSION    — notation-azure-kv plugin version (default: 1.2.0)
#
# Prereqs the caller must satisfy:
#   - `az` CLI is logged in (we call `az acr login` here).
#   - The runner's AAD identity has data-plane "Key Vault Crypto User"
#     on the signing key, and the cert/key is already provisioned in
#     the vault by the FTE-owned cert authority workflow.
#
# Usage (local):
#   ACR_NAME=__ACR_NAME__ \
#   IMAGE_REF=__ACR_NAME__.azurecr.io/__PROJECT_NAME__:dev \
#   SIGNING_KEY_VAULT_NAME=__PROJECT_NAME__-signing-kv \
#   SIGNING_KEY_NAME=__PROJECT_NAME__-signer \
#   ./scripts/sign-image.sh

set -euo pipefail

: "${ACR_NAME:?ACR_NAME is required}"
: "${IMAGE_REF:?IMAGE_REF is required}"
: "${SIGNING_KEY_VAULT_NAME:?SIGNING_KEY_VAULT_NAME is required}"
: "${SIGNING_KEY_NAME:?SIGNING_KEY_NAME is required}"

NOTATION_VERSION="${NOTATION_VERSION:-1.2.0}"
NOTATION_AKV_VERSION="${NOTATION_AKV_VERSION:-1.2.0}"
SIGNING_KEY_VERSION="${SIGNING_KEY_VERSION:-}"

INSTALL_DIR="${HOME}/.local/bin"
PLUGIN_DIR="${HOME}/.config/notation/plugins/azure-kv"

mkdir -p "$INSTALL_DIR" "$PLUGIN_DIR"
export PATH="$INSTALL_DIR:$PATH"

# Install notation CLI (idempotent)
if ! command -v notation >/dev/null 2>&1; then
  echo "Installing notation v${NOTATION_VERSION}..."
  curl -sSL "https://github.com/notaryproject/notation/releases/download/v${NOTATION_VERSION}/notation_${NOTATION_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C "$INSTALL_DIR" notation
  chmod +x "$INSTALL_DIR/notation"
fi

# Install notation-azure-kv plugin (idempotent). Notation discovers
# plugins via `${XDG_CONFIG_HOME:-~/.config}/notation/plugins/<name>/notation-<name>`.
if [ ! -x "${PLUGIN_DIR}/notation-azure-kv" ]; then
  echo "Installing notation-azure-kv plugin v${NOTATION_AKV_VERSION}..."
  curl -sSL "https://github.com/Azure/notation-azure-kv/releases/download/v${NOTATION_AKV_VERSION}/notation-azure-kv_${NOTATION_AKV_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C "$PLUGIN_DIR" notation-azure-kv
  chmod +x "${PLUGIN_DIR}/notation-azure-kv"
fi

notation version
notation plugin list

# Notation pushes signature artifacts back to ACR over the registry API,
# so it needs an authenticated docker config.
az acr login --name "${ACR_NAME}"

# Build the full Key Vault key identifier. Notation accepts either a
# versioned URI (pins signatures to that key version) or unversioned
# (uses latest). We default to unversioned so cert rotation in AKV
# doesn't require a pipeline edit.
KEY_ID="https://${SIGNING_KEY_VAULT_NAME}.vault.azure.net/keys/${SIGNING_KEY_NAME}"
if [ -n "${SIGNING_KEY_VERSION}" ]; then
  KEY_ID="${KEY_ID}/${SIGNING_KEY_VERSION}"
fi

SIGN_ARGS=(
  --signature-format cose
  --plugin azure-kv
  --id "${KEY_ID}"
)
if [ -n "${NOTATION_PLUGIN_CONFIG:-}" ]; then
  SIGN_ARGS+=(--plugin-config "${NOTATION_PLUGIN_CONFIG}")
fi

echo "Signing ${IMAGE_REF} with ${KEY_ID}"
notation sign "${SIGN_ARGS[@]}" "${IMAGE_REF}"

echo "##[section]Signed: ${IMAGE_REF}"
notation list "${IMAGE_REF}"
