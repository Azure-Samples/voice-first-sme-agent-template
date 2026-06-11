#!/usr/bin/env bash
# ============================================================================
# One-time RAG indexer setup for __PROJECT_NAME__ (Phase G, 2026-05-22).
#
# Run this ONCE after the first bicep deploy with features.rag=true. It:
#   1. Approves the AI-Search-managed Shared Private Link (SPL) connections
#      that land in "Pending" state on the RAG storage account + embeddings
#      account (Search can't reach them privately until these are approved).
#   2. Polls AI Search's sharedPrivateLinkResources/spl-rag-{storage,embeddings}
#      until provisioningState=Succeeded AND status=Approved.
#   3. Adds the runner's current public IP to the Search firewall (so step 4
#      can PUT data-plane resources over the public REST endpoint — the
#      Container App still reaches Search via the private endpoint).
#   4. PUTs index → datasource → skillset → indexer (AAD auth via
#      `az rest --resource https://search.azure.com/`; matches the wizard's
#      vector-field name `text_vector` so the __PROJECT_NAME__ backend works without
#      extra env-var config).
#   5. Kicks off the indexer's first run.
#   6. Optionally removes the runner's IP from the Search firewall again.
#
# Idempotent — safe to re-run. Each run drops + recreates the indexer /
# skillset / index to converge to the canonical schema, then triggers a full
# reindex.
#
# Prerequisites:
#   - az cli logged in as a principal with:
#       * Reader on __PROJECT_NAME__-partners-vnet-rg (to list PEs/SPLs)
#       * Permission to approve PE connections on the targets
#       * Search Service Contributor on the AI Search service (to PUT
#         indexer/index/skillset/datasource over the data plane — needed
#         because disableLocalAuth=true on the Search service)
#       * Network Contributor or equivalent on the AI Search service
#         (to update networkRuleSet.ipRules in step 3)
#   - The bicep deploy with features.rag=true has already provisioned:
#       - __PROJECT_NAME__-partners-search-vnet (AI Search)
#       - agentppartnersragstg (storage)
#       - __PROJECT_NAME__-partners-embed (embeddings, with text-embedding-3-large)
#       - Search PE, Storage PE, Embeddings PE on pe-subnet
#       - The two Search-managed SPLs (spl-rag-storage, spl-rag-embeddings)
#   - The deploy.js logic in templates/public-repo/setup-wizard/ has been
#     verified to work end-to-end (this script ports the __PROJECT_NAME__-specific
#     parts of that flow into a standalone runnable).
#
# Usage:
#   bash scripts/setup-rag-indexer.sh
# ============================================================================
set -euo pipefail

# ---------- configuration (must match agents/__PROJECT_NAME__/main.bicepparam) ----------
SEARCH_SERVICE="__PROJECT_NAME__-partners-search-vnet"
SEARCH_RG="__PROJECT_NAME__-partners-vnet-rg"
STORAGE_ACCOUNT="agentppartnersragstg"
STORAGE_CONTAINER="rag-documents"
EMBEDDINGS_ACCOUNT="__PROJECT_NAME__-partners-embed"
INDEX_NAME="rag-index"
INDEXER_NAME="rag-indexer"
DATASOURCE_NAME="rag-datasource"
SKILLSET_NAME="rag-skillset"
EMBEDDING_DEPLOYMENT="text-embedding-3-large"
EMBEDDING_MODEL="text-embedding-3-large"
EMBEDDING_DIMS=3072
API_VERSION="2024-07-01"
SUBSCRIPTION_ID="__SUBSCRIPTION_ID__"

SEARCH_ENDPOINT="https://${SEARCH_SERVICE}.search.windows.net"
# NOTE: use *.openai.azure.com here, NOT *.cognitiveservices.azure.com.
# The Search-side Shared Private Link uses groupId='openai_account',
# which provides a private path only for the openai.azure.com FQDN.
# The cognitiveservices.azure.com hostname is *not* covered by the SPL,
# so the indexer's skillset and the query-time vectorizer would both
# attempt a public call to a PNA=Disabled embeddings account and get 403.
EMBEDDINGS_ENDPOINT="https://${EMBEDDINGS_ACCOUNT}.openai.azure.com"
STORAGE_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${SEARCH_RG}/providers/Microsoft.Storage/storageAccounts/${STORAGE_ACCOUNT}"
EMBEDDINGS_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${SEARCH_RG}/providers/Microsoft.CognitiveServices/accounts/${EMBEDDINGS_ACCOUNT}"
SEARCH_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${SEARCH_RG}/providers/Microsoft.Search/searchServices/${SEARCH_SERVICE}"

# AI Search bicep sets a unique requestMessage on SPLs:
# `agent-template-spl-<searchName>/<groupId>`. We filter on that prefix
# when approving Pending PE connections, so we don't touch unrelated PEs
# an admin may have manually set up on the same targets.
SPL_MARKER_PREFIX="agent-template-spl-${SEARCH_SERVICE}"

# ---------- helpers ----------
log() { echo "[setup-rag] $*"; }
die() { echo "[setup-rag] ERROR: $*" >&2; exit 1; }

# ---------- step 0: sanity check that bicep resources exist ----------
log "Checking bicep-deployed resources exist..."
az search service show -n "$SEARCH_SERVICE" -g "$SEARCH_RG" --query "name" -o tsv > /dev/null \
  || die "Search service ${SEARCH_SERVICE} not found in ${SEARCH_RG}. Run the bicep deploy with features.rag=true first."
az storage account show -n "$STORAGE_ACCOUNT" -g "$SEARCH_RG" --query "name" -o tsv > /dev/null \
  || die "Storage ${STORAGE_ACCOUNT} not found."
az cognitiveservices account show -n "$EMBEDDINGS_ACCOUNT" -g "$SEARCH_RG" --query "name" -o tsv > /dev/null \
  || die "Embeddings ${EMBEDDINGS_ACCOUNT} not found."
log "  All three resources exist."

# Verify the embedding deployment exists on the embeddings account. The
# bicep module `rag-embeddings.bicep` creates this automatically when it
# owns the account.
EMBEDDING_DEPLOYMENT_NAME=$(az cognitiveservices account deployment list \
  -n "$EMBEDDINGS_ACCOUNT" -g "$SEARCH_RG" \
  --query "[?properties.model.name=='${EMBEDDING_MODEL}'].name | [0]" -o tsv || echo "")
if [ -z "$EMBEDDING_DEPLOYMENT_NAME" ] || [ "$EMBEDDING_DEPLOYMENT_NAME" = "None" ]; then
  die "No ${EMBEDDING_MODEL} deployment found on ${EMBEDDINGS_ACCOUNT}. Verify rag-embeddings.bicep ran."
fi
log "  Embedding deployment: ${EMBEDDING_DEPLOYMENT_NAME}"
EMBEDDING_DEPLOYMENT="$EMBEDDING_DEPLOYMENT_NAME"

# ---------- step 1: approve SPL Pending PE connections on storage + embeddings ----------
approve_spl_pending() {
  local target_id="$1"
  local label="$2"
  log "[${label}] listing PE connections..."
  local attempts=0
  local connections="[]"
  while [ "$attempts" -lt 5 ]; do
    connections=$(az network private-endpoint-connection list --id "$target_id" -o json 2>/dev/null || echo "[]")
    local has_match
    has_match=$(echo "$connections" | python3 -c '
import sys, json
data = json.load(sys.stdin)
prefix = sys.argv[1]
matches = [c for c in data if (c.get("properties", {}).get("privateLinkServiceConnectionState", {}).get("description", "")).startswith(prefix)]
print("yes" if matches else "no")
' "$SPL_MARKER_PREFIX" | tr -d '\r')
    if [ "$has_match" = "yes" ]; then break; fi
    attempts=$((attempts + 1))
    log "[${label}]   waiting for SPL connection to propagate (attempt ${attempts}/5)..."
    sleep 5
  done

  echo "$connections" | python3 -c '
import sys, json
data = json.load(sys.stdin)
prefix = sys.argv[1]
for c in data:
    state = c.get("properties", {}).get("privateLinkServiceConnectionState", {})
    if not (state.get("description") or "").startswith(prefix):
        continue
    status = state.get("status") or ""
    print("\t".join([c["id"], c["name"], status]))
' "$SPL_MARKER_PREFIX" | tr -d '\r' | while IFS=$'\t' read -r conn_id conn_name conn_status; do
    if [ "$conn_status" = "Approved" ]; then
      log "[${label}]   [skip] ${conn_name} already Approved"
      continue
    fi
    if [ "$conn_status" != "Pending" ]; then
      log "[${label}]   WARNING — ${conn_name} in unexpected state '${conn_status}', leaving alone."
      continue
    fi
    log "[${label}]   approving ${conn_name} ..."
    az network private-endpoint-connection approve \
      --id "$conn_id" \
      --description "Approved by setup-rag-indexer.sh ($(date -u +%FT%TZ))" \
      -o none
  done
}

log "Approving SPL Pending PE connections..."
approve_spl_pending "$STORAGE_RESOURCE_ID" "Storage"
approve_spl_pending "$EMBEDDINGS_RESOURCE_ID" "Embeddings"

# ---------- step 2: poll Search-side SPL resources until Succeeded+Approved ----------
poll_spl_approved() {
  local spl_name="$1"
  local label="$2"
  local timeout=300
  local start
  start=$(date +%s)
  local last_state=""
  local last_status=""
  while true; do
    local now elapsed
    now=$(date +%s)
    elapsed=$((now - start))
    if [ "$elapsed" -ge "$timeout" ]; then
      log "[${label}] WARNING — SPL not (Succeeded, Approved) after ${timeout}s. Last: provisioning=${last_state}, status=${last_status}. Indexer may fail until propagated."
      return 1
    fi
    local resp
    resp=$(az rest --method GET \
      --uri "https://management.azure.com${SEARCH_RESOURCE_ID}/sharedPrivateLinkResources/${spl_name}?api-version=2024-03-01-preview" \
      -o json 2>/dev/null || echo "{}")
    last_state=$(echo "$resp" | python3 -c 'import sys, json; d=json.load(sys.stdin); print((d.get("properties") or {}).get("provisioningState") or "")' | tr -d '\r')
    last_status=$(echo "$resp" | python3 -c 'import sys, json; d=json.load(sys.stdin); print((d.get("properties") or {}).get("status") or "")' | tr -d '\r')
    if [ "$last_state" = "Succeeded" ] && [ "$last_status" = "Approved" ]; then
      log "[${label}] SPL ready (provisioning=${last_state}, status=${last_status})"
      return 0
    fi
    if [ "$last_state" = "Failed" ]; then
      die "[${label}] SPL provisioning Failed"
    fi
    sleep 5
  done
}

log "Polling Search-side SPL resources..."
poll_spl_approved "spl-rag-storage" "Storage"
poll_spl_approved "spl-rag-embeddings" "Embeddings"

# ---------- step 3: add runner IP to Search firewall ----------
# LOCAL OVERRIDE: SETUP_RAG_SKIP_SEARCH_FW=1 skips the Search firewall change
# (used when running from a network that NATs the runner to a different IP
# than what api.ipify.org reports, e.g. Microsoft corp net). The caller is
# expected to have already set Search publicNetworkAccess=Enabled with
# an ipRules list that includes them, or to be running on the SPL).
if [ "${SETUP_RAG_SKIP_SEARCH_FW:-0}" = "1" ]; then
  log "SETUP_RAG_SKIP_SEARCH_FW=1 set — skipping Search firewall update"
  RUNNER_IP=$(curl -fsS https://api.ipify.org 2>/dev/null || curl -fsS https://ifconfig.me 2>/dev/null || echo "")
  log "  (informational) runner public IP from ipify: ${RUNNER_IP:-unknown}"
else
RUNNER_IP=$(curl -fsS https://api.ipify.org 2>/dev/null || curl -fsS https://ifconfig.me 2>/dev/null || echo "")
if [ -z "$RUNNER_IP" ]; then
  die "Could not detect runner public IP for Search firewall allow-list. Set it manually via 'az search service update --add networkRuleSet.ipRules ...' then re-run."
fi
log "Runner public IP detected: ${RUNNER_IP}"
log "Adding runner IP to Search firewall (allow-list)..."
az search service update \
  -n "$SEARCH_SERVICE" -g "$SEARCH_RG" \
  --set "networkRuleSet.ipRules=[{\"value\":\"${RUNNER_IP}\"}]" \
  -o none

# Storage too — needed if you also want to upload sample docs from this
# same machine. Idempotent (Azure de-dupes IP rules).
log "Adding runner IP to Storage firewall (for ad-hoc upload from this machine)..."
az storage account network-rule add \
  -g "$SEARCH_RG" --account-name "$STORAGE_ACCOUNT" \
  --ip-address "$RUNNER_IP" -o none 2>/dev/null || true

# Brief settle delay — Search firewall updates can take ~30s to propagate
# to the front door.
log "  Waiting 30s for Search firewall propagation..."
sleep 30
fi

# ---------- step 4: PUT index → datasource → skillset → indexer ----------
search_put() {
  local resource_path="$1"
  local label="$2"
  local body_file="$3"
  # On Git Bash / MSYS, convert the Linux path to a Windows path so that
  # az.exe (a native Windows CLI) can read the file. cygpath is a no-op
  # on systems where it doesn't exist (real Linux CI), so this is safe.
  if command -v cygpath > /dev/null 2>&1; then
    body_file=$(cygpath -w "$body_file")
  fi
  log "  PUT ${resource_path}"
  az rest --method PUT \
    --uri "${SEARCH_ENDPOINT}${resource_path}?api-version=${API_VERSION}" \
    --resource "https://search.azure.com/" \
    --headers "Content-Type=application/json" \
    --body "@${body_file}" \
    -o none \
    || die "${label} PUT failed"
}

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# 4a. Index — schema matches the wizard (text_vector field name, 3072-dim
# vectors for text-embedding-3-large). __PROJECT_NAME__ backend's
# AZURE_SEARCH_VECTOR_FIELD default is `text_vector` so no extra env-var
# wiring is needed.
log "Resetting existing pipeline (idempotent)..."
for resource in "indexers/${INDEXER_NAME}" "skillsets/${SKILLSET_NAME}" "indexes/${INDEX_NAME}" "datasources/${DATASOURCE_NAME}"; do
  az rest --method DELETE \
    --uri "${SEARCH_ENDPOINT}/${resource}?api-version=${API_VERSION}" \
    --resource "https://search.azure.com/" \
    -o none 2>/dev/null \
    && log "  deleted ${resource}" \
    || log "  ${resource} not present (ok)"
done

# 4a. Datasource — ResourceId= form (Search MSI auth) since
# allowSharedKeyAccess=false on the storage account.
cat > "${TMP_DIR}/datasource.json" <<EOF
{
  "name": "${DATASOURCE_NAME}",
  "type": "azureblob",
  "credentials": { "connectionString": "ResourceId=${STORAGE_RESOURCE_ID};" },
  "container": { "name": "${STORAGE_CONTAINER}" },
  "dataDeletionDetectionPolicy": {
    "@odata.type": "#Microsoft.Azure.Search.NativeBlobSoftDeleteDeletionDetectionPolicy"
  }
}
EOF
search_put "/datasources/${DATASOURCE_NAME}" "datasource" "${TMP_DIR}/datasource.json"

# 4b. Index — schema matches the wizard byte-for-byte (text_vector field
# name, 3072-dim vectors for text-embedding-3-large, HNSW parameters,
# semantic config named 'default'). __PROJECT_NAME__ backend's
# AZURE_SEARCH_VECTOR_FIELD default is `text_vector` so no extra env-var
# wiring is needed. Field ordering matches deploy.js for parity.
cat > "${TMP_DIR}/index.json" <<EOF
{
  "name": "${INDEX_NAME}",
  "fields": [
    { "name": "chunk_id",  "type": "Edm.String", "key": true,  "retrievable": true, "filterable": true, "sortable": true, "analyzer": "keyword" },
    { "name": "parent_id", "type": "Edm.String", "filterable": true, "retrievable": true },
    { "name": "title",     "type": "Edm.String", "searchable": true, "retrievable": true },
    { "name": "chunk",     "type": "Edm.String", "searchable": true, "retrievable": true },
    { "name": "source",    "type": "Edm.String", "retrievable": true, "filterable": true },
    {
      "name": "text_vector",
      "type": "Collection(Edm.Single)",
      "searchable": true,
      "retrievable": false,
      "stored": true,
      "dimensions": ${EMBEDDING_DIMS},
      "vectorSearchProfile": "rag-vector-profile"
    }
  ],
  "vectorSearch": {
    "algorithms": [
      {
        "name": "rag-hnsw",
        "kind": "hnsw",
        "hnswParameters": { "m": 4, "efConstruction": 400, "efSearch": 500, "metric": "cosine" }
      }
    ],
    "profiles": [
      { "name": "rag-vector-profile", "algorithm": "rag-hnsw", "vectorizer": "rag-vectorizer" }
    ],
    "vectorizers": [
      {
        "name": "rag-vectorizer",
        "kind": "azureOpenAI",
        "azureOpenAIParameters": {
          "resourceUri": "${EMBEDDINGS_ENDPOINT}",
          "deploymentId": "${EMBEDDING_DEPLOYMENT}",
          "modelName": "${EMBEDDING_MODEL}"
        }
      }
    ]
  },
  "semantic": {
    "configurations": [
      {
        "name": "default",
        "prioritizedFields": {
          "titleField": { "fieldName": "title" },
          "prioritizedContentFields": [ { "fieldName": "chunk" } ]
        }
      }
    ]
  }
}
EOF
search_put "/indexes/${INDEX_NAME}" "index" "${TMP_DIR}/index.json"

# 4c. Skillset — Split + AzureOpenAIEmbedding + indexProjections, same
# shape as the wizard's deploy.js.
cat > "${TMP_DIR}/skillset.json" <<EOF
{
  "name": "${SKILLSET_NAME}",
  "description": "Crack PDFs, split into pages, embed each page.",
  "skills": [
    {
      "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
      "name": "split",
      "description": "Split text into pages with overlap.",
      "context": "/document",
      "textSplitMode": "pages",
      "maximumPageLength": 2000,
      "pageOverlapLength": 500,
      "inputs": [ { "name": "text", "source": "/document/content" } ],
      "outputs": [ { "name": "textItems", "targetName": "pages" } ]
    },
    {
      "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
      "name": "embed",
      "description": "Embed each page with Azure OpenAI.",
      "context": "/document/pages/*",
      "resourceUri": "${EMBEDDINGS_ENDPOINT}",
      "deploymentId": "${EMBEDDING_DEPLOYMENT}",
      "modelName": "${EMBEDDING_MODEL}",
      "dimensions": ${EMBEDDING_DIMS},
      "inputs": [ { "name": "text", "source": "/document/pages/*" } ],
      "outputs": [ { "name": "embedding", "targetName": "text_vector" } ]
    }
  ],
  "indexProjections": {
    "selectors": [
      {
        "targetIndexName": "${INDEX_NAME}",
        "parentKeyFieldName": "parent_id",
        "sourceContext": "/document/pages/*",
        "mappings": [
          { "name": "chunk",       "source": "/document/pages/*" },
          { "name": "text_vector", "source": "/document/pages/*/text_vector" },
          { "name": "title",       "source": "/document/metadata_storage_name" },
          { "name": "source",      "source": "/document/metadata_storage_path" }
        ]
      }
    ],
    "parameters": { "projectionMode": "skipIndexingParentDocuments" }
  }
}
EOF
search_put "/skillsets/${SKILLSET_NAME}" "skillset" "${TMP_DIR}/skillset.json"

# 4d. Indexer — executionEnvironment=private so the indexer fleet runs
# inside Search's private execution path and uses the SPLs to reach
# storage + embeddings. Empty fieldMappings/outputFieldMappings since
# all chunk-level mappings are in the skillset's indexProjections above.
cat > "${TMP_DIR}/indexer.json" <<EOF
{
  "name": "${INDEXER_NAME}",
  "dataSourceName": "${DATASOURCE_NAME}",
  "targetIndexName": "${INDEX_NAME}",
  "skillsetName": "${SKILLSET_NAME}",
  "schedule": { "interval": "PT5M" },
  "parameters": {
    "configuration": {
      "dataToExtract": "contentAndMetadata",
      "parsingMode": "default",
      "imageAction": "none",
      "executionEnvironment": "private"
    }
  },
  "fieldMappings": [],
  "outputFieldMappings": []
}
EOF
search_put "/indexers/${INDEXER_NAME}" "indexer" "${TMP_DIR}/indexer.json"

# ---------- step 5: kick off initial run ----------
log "Kicking off initial indexer run..."
az rest --method POST \
  --uri "${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}/run?api-version=${API_VERSION}" \
  --resource "https://search.azure.com/" \
  -o none \
  || log "  WARNING — initial run kickoff returned non-zero (the scheduled run every 5 min will retry)"

# ---------- step 6: brief status check ----------
log "Indexer status (first run may still be in-progress):"
az rest --method GET \
  --uri "${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}/status?api-version=${API_VERSION}" \
  --resource "https://search.azure.com/" \
  --query "{last:lastResult.status, executionHistory:executionHistory[0:1].[status, errorMessage]}" \
  -o json || true

log ""
log "================================================================"
log "  RAG indexer setup complete."
log ""
log "  Next steps for the team:"
log "  1. Upload PDFs/docs to the rag-documents container:"
log "       az storage blob upload-batch \\"
log "         --account-name ${STORAGE_ACCOUNT} \\"
log "         --destination ${STORAGE_CONTAINER} \\"
log "         --source ./docs --auth-mode login"
log "     (Each team member's workstation IP must be allow-listed on"
log "     storage. Set the RAG_STORAGE_ALLOWED_IPS ADO variable to a"
log "     comma-separated list so future deploys keep them.)"
log "  2. The indexer picks up new blobs every 5 minutes. Force a run:"
log "       az rest --method POST \\"
log "         --uri ${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}/run?api-version=${API_VERSION} \\"
log "         --resource https://search.azure.com/"
log "  3. Watch status:"
log "       az rest --method GET \\"
log "         --uri ${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}/status?api-version=${API_VERSION} \\"
log "         --resource https://search.azure.com/"
log "================================================================"
