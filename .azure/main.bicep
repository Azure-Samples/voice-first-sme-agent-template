// Unified infrastructure deployment for the Agent Template.
//
// Two consumers:
//   1. setup-wizard/deploy.js (external partner deployment) — provisions
//      everything from scratch using settings.json values.
//   2. pipeline.yml (internal Agent P pipeline) — reuses shared
//      platform-team resources (ACR, Container App Environment, Voice
//      Live account) by passing the `existing*` params; only provisions
//      the per-agent bits (Container App, identity, optional Cosmos /
//      Search).
//
// `__PROJECT_NAME__`, `__OPENAI_ACCOUNT_NAME__`, or any other Microsoft-internal name MUST
// NOT appear here — those names live in pipeline.yml + the internal
// bicepparam files, never in this template.

targetScope = 'resourceGroup'

// ---------- core identity / naming ----------
@description('Project name (also Container App name).')
param projectName string

@description('Azure region.')
param location string = resourceGroup().location

@description('Region for RAG resources (Search, Storage, embeddings). Defaults to location. Set to a different region if location is at capacity for AI Search basic tier.')
param ragLocation string = ''

@description('Stage label baked into env vars (dev / prod).')
param stage string = 'dev'

// ---------- naming ----------
@description('ACR name (globally unique). Used only when provisioning a new ACR (existingAcrName empty).')
param acrName string = ''

@description('Container App Environment name. Used only when provisioning a new env (existingContainerAppEnvName empty).')
param envName string

@description('Log Analytics workspace name.')
param logAnalyticsName string

@description('User-assigned managed identity name.')
param appIdentityName string

@description('Key Vault name (globally unique, 3-24 chars, alphanumeric + dashes, must start with a letter). Empty derives a deterministic name from projectName + a hash of the RG ID.')
param keyVaultName string = ''

@description('Expose the OpenAPI JSON spec at /openapi.json on the deployed app (defaults off; flip on for environments onboarded to URSA Web Scanner).')
param exposeOpenApiSpec bool = false

// ---------- image ----------
@description('Image reference appended to the ACR login server. Accepts tag form ("__PROJECT_NAME__:0.1.0-abc123") or digest form ("__PROJECT_NAME__@sha256:..."). deploy.js prefers digest form so Container App revisions are pinned to immutable images for rollback.')
param imageName string

@description('Resolved image digest ("sha256:..."), surfaced to the running container as IMAGE_DIGEST so /api/health can report the immutable byte identity of the deployed image. Empty when the caller cannot resolve a digest (e.g. internal ADO path that bypasses deploy.js).')
param imageDigest string = ''

// ---------- MSAL ----------
param msalClientId string
param msalTenantId string

@description('Optional second MSAL client ID (e.g. for a partner / tribe tenant). Empty to skip.')
param msalTribeClientId string = ''

@description('Optional second MSAL tenant ID (e.g. for a partner / tribe tenant). Empty to skip.')
param msalTribeTenantId string = ''

// ---------- voice live ----------
@description('Voice Live mode: create | existing')
@allowed([ 'create', 'existing' ])
param voiceLiveMode string = 'create'

@description('Cognitive Services account name (globally unique). Used only when voiceLiveMode=create.')
param voiceLiveAccountName string = ''

@description('Existing Voice Live endpoint (required if voiceLiveMode=existing).')
param existingVoiceLiveEndpoint string = ''

@description('Voice Live model.')
param voiceLiveModel string = 'gpt-realtime'

@description('Voice Live model version. Pinned to a known-good value because some regions (e.g. Sweden Central) reject deployments when version is null. Set to empty string only if a region accepts a null version for this model.')
param voiceLiveModelVersion string = '2025-08-28'

@description('Standard Azure voice used by both the live Voice Live session and the pre-generated filler/nudge/greeting audio. Picked in the setup wizard. Format: <locale>-<name>:<model> (e.g. en-US-Andrew:DragonHDLatestNeural). Browse the catalog at https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=tts.')
param voiceLiveVoice string = 'en-US-Andrew:DragonHDLatestNeural'

@secure()
@description('Voice Live API key. Required when voiceLiveAuthMode=key. Ignored (but still safe to pass) when voiceLiveAuthMode=mi.')
param voiceLiveApiKey string

@description('Voice Live auth mode. "key" uses the API key (legacy). "mi" uses Microsoft Entra: the Container App\'s UAMI is granted Cognitive Services User on the account and the backend authenticates with a Bearer token.')
@allowed([ 'key', 'mi' ])
param voiceLiveAuthMode string = 'key'

@description('Existing Voice Live account name. Required when voiceLiveMode=existing AND voiceLiveAuthMode=mi (so we can grant the role on the right account). Ignored when voiceLiveMode=create (the new account name is used).')
param existingVoiceLiveAccountName string = ''

@description('Resource group of the existing Voice Live account. Defaults to current RG. Set this when the account lives in a different RG (e.g. shared `__OPENAI_ACCOUNT_NAME__` in `__PROJECT_NAME__-rg`).')
param existingVoiceLiveResourceGroup string = ''

@description('Optional override for the Voice Live private endpoint name. Used to coexist with a legacy hand-created PE that already occupies the default name (projectName + "-voicelive-pe") — Azure refuses CannotChangePrivateLinkConnectionOnPrivateEndpoint on an in-place retarget, so the new PE picks a distinct name and the legacy one is left alone as a rollback fallback. Leave empty to use the default name that matches the other PEs in this template.')
param voiceLivePeName string = ''

// ---------- reuse existing platform resources (pipeline use case) ----------
@description('Existing ACR name. When set, skip provisioning ACR and grant AcrPull on this registry instead. Pair with existingAcrResourceGroup when it lives in a different RG.')
param existingAcrName string = ''

@description('Resource group of the existing ACR. Defaults to current RG when existingAcrName is set but this is empty.')
param existingAcrResourceGroup string = ''

@description('Existing Container App Environment name. When set, skip provisioning env and reuse this one.')
param existingContainerAppEnvName string = ''

@description('Resource group of the existing Container App Environment. Defaults to current RG.')
param existingContainerAppEnvResourceGroup string = ''

// ---------- optional features ----------
@description('Feature toggles. Drives conditional module inclusion.')
param features object = {
  rag: false
  customDomain: false
  customVoice: false
  appInsights: false
  transcripts: false
}

@description('Custom domain name (only used when features.customDomain).')
param customDomainName string = ''

@description('Existing managed cert name in the ACA env for the custom domain.')
param customDomainCertName string = ''

@description('Azure Personal Voice Speaker Profile ID (GUID). Only used when features.customVoice. Surfaced to the Container App as AZURE_PERSONAL_VOICE_PROFILE_ID; the backend reads it in app/config.py and surfaces it to the frontend via /api/voice-config, which switches the Voice Live session to the azure-personal voice schema. Leave empty to keep the standard voice (e.g. while waiting for Personal Voice access to be approved).')
param personalVoiceProfileId string = ''

@description('AI Search service name (only used when features.rag). Globally unique.')
param searchServiceName string = ''

@description('RAG storage account name (only used when features.rag). 3-24 lowercase, globally unique.')
param ragStorageAccountName string = ''

@description('RAG blob container name. Users drop docs here.')
param ragContainerName string = 'rag-documents'

@description('Additional IPv4 addresses (or CIDR ranges) allow-listed on the RAG storage account network ACL when features.rag and networkConfig.enabled. Use this to grant team-member workstations upload access. Survives redeploys (out-of-band `az storage account network-rule add` would otherwise be wiped on each bicep run).')
param ragStorageAllowedIpRules array = []

@description('Embeddings OpenAI account name (only used when features.rag and no existing endpoint provided). Globally unique.')
param ragEmbeddingsAccountName string = ''

@description('Embedding model. Default text-embedding-3-large (3072 dims).')
param ragEmbeddingModel string = 'text-embedding-3-large'

@description('Existing embeddings endpoint (e.g. https://my-ai.cognitiveservices.azure.com). When set, skip creating a new account — reuses an existing AIServices account.')
param ragEmbeddingsExistingEndpoint string = ''

@description('Existing embeddings deployment name on the existing account.')
param ragEmbeddingsExistingDeployment string = ''

@description('Direct AI Search endpoint. When set, skip provisioning Search and wire this endpoint into the Container App env vars. Used when reusing a pre-existing Search service (e.g. a shared one owned by a platform team).')
param existingSearchEndpoint string = ''

@description('AI Search index name (used regardless of feature.rag mode).')
param searchIndexName string = 'rag-index'

@description('AI Search semantic config name. Empty disables semantic search.')
param searchSemanticConfig string = 'default'

@description('Object ID of the user/SP running this deploy. When set, granted role assignments where applicable so the human can use the portal Data Explorer (Cosmos), upload PDFs (RAG storage), etc.')
param deployerObjectId string = ''

@description('Principal type of deployerObjectId.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerPrincipalType string = 'User'

// ---------- networking (optional private-egress mode) ----------
@description('Network configuration. Set { enabled: true } to provision a VNet and route Container App egress through private endpoints. Public ingress on the Container App is preserved. Object shape: { enabled: bool, addressSpace: string, subnets: { aca: string, privateEndpoints: string }, deployerIpAddress: string }.')
param networkConfig object = {
  enabled: false
}

@description('Pass-2-only flag. When true (and networkConfig.enabled), flips ACR to publicNetworkAccess: Disabled. The ACR PE is already wired in pass 1 so the Container App\'s image pull goes through the private endpoint. Has no effect when networkConfig.enabled is false.')
param acrLockdown bool = false

@description('Pass-2-only flag. When true (and networkConfig.enabled + features.rag), flips AI Search to publicNetworkAccess: Disabled. The Search PE is already wired in pass 1, and indexer setup has already run via the deployer-IP firewall window. Has no effect when networkConfig.enabled or features.rag is false.')
param searchLockdown bool = false

@description('Whether to deploy the Container App. False on the first pass (before the image exists in ACR), true on the second pass.')
param deployApp bool = true

@description('First line the agent says.')
param agentGreeting string = ''

@description('Path inside the container to the system prompt file.')
param systemPromptFile string = ''

@description('Path inside the container to the knowledge base JSON file.')
param knowledgeBaseFile string = ''

@description('Path inside the container to the partners registry JSON.')
param partnersFile string = ''

@description('Cosmos DB account name (only used when features.transcripts).')
param cosmosAccountName string = ''

@description('Cosmos DB database name for transcripts.')
param cosmosDatabaseName string = 'transcripts'

@description('Cosmos DB container name for transcripts.')
param cosmosContainerName string = 'sessions'

@description('Default TTL in seconds for transcript docs. -1 disables auto-expire.')
param cosmosDefaultTtlSeconds int = 2592000

@secure()
@description('Per-deployment salt used to hash user identifiers before storing transcripts.')
param transcriptSalt string = ''

// ---------- resolved naming ----------
var resolvedAcrResourceGroup = empty(existingAcrResourceGroup) ? resourceGroup().name : existingAcrResourceGroup
var resolvedEnvResourceGroup = empty(existingContainerAppEnvResourceGroup) ? resourceGroup().name : existingContainerAppEnvResourceGroup
var provisionAcr = empty(existingAcrName)
var provisionEnv = empty(existingContainerAppEnvName)
var resolvedVoiceLiveResourceGroup = empty(existingVoiceLiveResourceGroup) ? resourceGroup().name : existingVoiceLiveResourceGroup
var resolvedVoiceLiveAccountName = voiceLiveMode == 'create' ? voiceLiveAccountName : existingVoiceLiveAccountName
// Key Vault names are globally unique and capped at 24 chars. Truncate
// `<projectName>-kv-<hash>` to 24, where the hash is derived from the
// RG ID so re-deploys land on the same vault.
var resolvedKeyVaultName = !empty(keyVaultName) ? keyVaultName : take('${projectName}-kv-${uniqueString(resourceGroup().id)}', 24)

// ---------- network ----------
// Network mode is only meaningful when we own the resources we're
// attaching PEs to. When the caller passes an existing ACR / env /
// Voice Live, the network params on those modules are no-ops; the
// wizard blocks these combinations upstream, but the bicep just
// degrades gracefully here.
var networkEnabled = bool(networkConfig.?enabled ?? false)
var networkAddressSpace = string(networkConfig.?addressSpace ?? '10.20.0.0/16')
var networkAcaSubnet = string(networkConfig.?subnets.?aca ?? '10.20.0.0/27')
var networkPeSubnet = string(networkConfig.?subnets.?privateEndpoints ?? '10.20.1.0/24')
var networkDeployerIp = string(networkConfig.?deployerIpAddress ?? '')

// ---------- modules ----------

// Optional VNet + subnets + private DNS zones for private-egress mode.
module network 'modules/network.bicep' = if (networkEnabled) {
  name: 'network'
  params: {
    vnetName: '${projectName}-vnet'
    location: location
    addressSpace: networkAddressSpace
    acaSubnetCidr: networkAcaSubnet
    peSubnetCidr: networkPeSubnet
  }
}

module logs 'modules/log-analytics.bicep' = {
  name: 'logs'
  params: {
    name: logAnalyticsName
    location: location
  }
}

// Workspace transformation DCR that scrubs secret-bearing query
// parameters from ACA console logs at ingest time. Defense-in-depth
// on top of disabling uvicorn access logs in the Dockerfile.
module logScrubDcr 'modules/log-scrub-dcr.bicep' = {
  name: 'logScrubDcr'
  params: {
    logAnalyticsWorkspaceName: logAnalyticsName
    logAnalyticsWorkspaceId: logs.outputs.id
    location: location
    dcrName: '${projectName}-log-scrub-dcr'
  }
}

// ACR — only provisioned when no existing one is provided.
module acr 'modules/acr.bicep' = if (provisionAcr) {
  name: 'acr'
  params: {
    name: acrName
    location: location
    networkEnabled: networkEnabled
    lockdown: acrLockdown
  }
}

// UAMI for the Container App.
module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: appIdentityName
    location: location
  }
}

// Key Vault for application secrets. Container App secret entries
// reference these via keyVaultUrl; values are fetched at revision
// creation using the UAMI (see keyVaultRole below).
module keyVault 'modules/key-vault.bicep' = {
  name: 'keyVault'
  params: {
    name: resolvedKeyVaultName
    location: location
    voiceLiveApiKey: voiceLiveAuthMode == 'key' ? voiceLiveApiKey : ''
    appInsightsConnectionString: features.appInsights ? appInsights!.outputs.connectionString : ''
    transcriptSalt: features.transcripts ? transcriptSalt : ''
  }
}

// Grant the Container App's UAMI permission to read secrets from KV.
module keyVaultRole 'modules/key-vault-role.bicep' = {
  name: 'keyVaultRole'
  params: {
    keyVaultName: keyVault.outputs.name
    uamiPrincipalId: identity.outputs.principalId
  }
}

// AcrPull grant on the target ACR. Scoped to a remote RG when reusing
// an ACR owned by a different RG (pipeline pattern).
module acrPullRole 'modules/acr-pull-role.bicep' = {
  name: 'acrPullRole'
  scope: resourceGroup(resolvedAcrResourceGroup)
  params: {
    acrName: provisionAcr ? acrName : existingAcrName
    uamiPrincipalId: identity.outputs.principalId
  }
  dependsOn: provisionAcr ? [ acr ] : []
}

// Optional: Application Insights for backend telemetry.
module appInsights 'modules/app-insights.bicep' = if (features.appInsights) {
  name: 'appInsights'
  params: {
    name: '${projectName}-appi'
    location: location
    workspaceId: logs.outputs.id
  }
}

// Container App Environment — only provisioned when no existing one is provided.
module env 'modules/container-app-env.bicep' = if (provisionEnv) {
  name: 'env'
  params: {
    name: envName
    location: location
    logAnalyticsCustomerId: logs.outputs.customerId
    logAnalyticsSharedKey: logs.outputs.primarySharedKey
    appInsightsConnectionString: features.appInsights ? appInsights!.outputs.connectionString : ''
    vnetSubnetId: networkEnabled ? network!.outputs.acaSubnetId : ''
  }
}

// Reference an existing environment when reusing.
resource existingEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = if (!provisionEnv) {
  name: existingContainerAppEnvName
  scope: resourceGroup(resolvedEnvResourceGroup)
}

var resolvedEnvId = provisionEnv ? env!.outputs.id : existingEnv!.id

// Voice Live: only created in 'create' mode.
module voiceLive 'modules/voice-live.bicep' = if (voiceLiveMode == 'create') {
  name: 'voiceLive'
  params: {
    accountName: voiceLiveAccountName
    location: location
    model: voiceLiveModel
    modelVersion: voiceLiveModelVersion
    networkEnabled: networkEnabled
  }
}

// Cognitive Services User grant on the Voice Live account — only when
// auth mode is 'mi'. Scoped to a remote RG when reusing an account
// owned by a different RG (pipeline pattern).
module voiceLiveRole 'modules/voice-live-role.bicep' = if (voiceLiveAuthMode == 'mi') {
  name: 'voiceLiveRole'
  scope: resourceGroup(resolvedVoiceLiveResourceGroup)
  params: {
    voiceLiveAccountName: resolvedVoiceLiveAccountName
    uamiPrincipalId: identity.outputs.principalId
  }
  dependsOn: voiceLiveMode == 'create' ? [ voiceLive ] : []
}

// Optional: AI Search for RAG.
module search 'modules/ai-search.bicep' = if (features.rag) {
  name: 'search'
  params: {
    name: searchServiceName
    location: ragLocation == '' ? location : ragLocation
    networkEnabled: networkEnabled
    deployerIpAddress: networkDeployerIp
    searchLockdown: searchLockdown
  }
}

// Storage account for RAG documents.
module ragStorage 'modules/rag-storage.bicep' = if (features.rag) {
  name: 'ragStorage'
  params: {
    name: ragStorageAccountName
    location: ragLocation == '' ? location : ragLocation
    containerName: ragContainerName
    searchPrincipalId: search!.outputs.principalId
    deployerObjectId: deployerObjectId
    deployerPrincipalType: deployerPrincipalType
    networkEnabled: networkEnabled
    deployerIpAddress: networkDeployerIp
    additionalAllowedIpRules: ragStorageAllowedIpRules
  }
}

// Embeddings: dedicated AIServices account with embeddings model.
module ragEmbeddings 'modules/rag-embeddings.bicep' = if (features.rag && ragEmbeddingsExistingEndpoint == '') {
  name: 'ragEmbeddings'
  params: {
    accountName: ragEmbeddingsAccountName
    location: ragLocation == '' ? location : ragLocation
    embeddingModel: ragEmbeddingModel
    searchPrincipalId: search!.outputs.principalId
    networkEnabled: networkEnabled
  }
}

// Grant the Container App's UAMI Search Index Data Reader on the search service.
module ragRoles 'modules/rag-roles.bicep' = if (features.rag) {
  name: 'ragRoles'
  params: {
    searchServiceName: search!.outputs.name
    uamiPrincipalId: identity.outputs.principalId
  }
}

// Search-managed Shared Private Links (network mode only).
//
// PR 3: the AI Search indexer reaches Storage + Embeddings privately
// via Search-managed PEs so we can lock those resources down. This is
// a separate module (not part of ai-search.bicep) because the SPL
// resources reference the targets (ragStorage / ragEmbeddings), which
// already depend on `search.outputs.principalId` — declaring the SPLs
// inside ai-search.bicep would create a circular module graph. Using
// an `existing` reference here decouples the order.
//
// Only created when an OWNED embeddings account exists (i.e.,
// ragEmbeddingsExistingEndpoint is empty). The wizard already blocks
// the network-mode + existing-embeddings combination, so this gate is
// belt-and-suspenders.
module searchSpl 'modules/search-shared-private-links.bicep' = if (features.rag && networkEnabled && ragEmbeddingsExistingEndpoint == '') {
  name: 'searchSpl'
  params: {
    searchServiceName: search!.outputs.name
    storageAccountId: ragStorage!.outputs.id
    embeddingsAccountId: ragEmbeddings!.outputs.id
  }
}

// Optional: Cosmos DB for transcripts.
module cosmos 'modules/cosmos.bicep' = if (features.transcripts) {
  name: 'cosmos'
  params: {
    accountName: cosmosAccountName
    location: location
    databaseName: cosmosDatabaseName
    containerName: cosmosContainerName
    defaultTtlSeconds: cosmosDefaultTtlSeconds
    networkEnabled: networkEnabled
  }
}

module cosmosRoles 'modules/cosmos-roles.bicep' = if (features.transcripts) {
  name: 'cosmosRoles'
  params: {
    cosmosAccountName: cosmos!.outputs.accountName
    uamiPrincipalId: identity.outputs.principalId
    deployerObjectId: deployerObjectId
  }
}

// ---------- private endpoints (network mode only) ----------
// PE location is the VNet's region (= the deployment `location` param),
// NOT `ragLocation`. PEs must live in the same region as the VNet /
// subnet, even when the target resource is in a different region.
//
// Only attach PEs to resources we own — if the caller passed an
// `existing*` name, PE lifecycle is the caller's responsibility (see
// PR 4 / __PROJECT_NAME__-partners migration for the shared-resource pattern).

module acrPe 'modules/private-endpoint.bicep' = if (networkEnabled && provisionAcr) {
  name: 'acrPe'
  params: {
    name: '${projectName}-acr-pe'
    location: location
    subnetId: network!.outputs.peSubnetId
    targetResourceId: acr!.outputs.id
    groupIds: [ 'registry' ]
    dnsZoneIds: [ network!.outputs.acrZoneId ]
  }
}

module voiceLivePe 'modules/private-endpoint.bicep' = if (networkEnabled && voiceLiveMode == 'create') {
  name: 'voiceLivePe'
  params: {
    // Default keeps the `${projectName}-<service>-pe` convention used
    // by the other PEs. An existing deployment can override via
    // `voiceLivePeName` when a legacy hand-created PE already owns the
    // default name (see the param doc).
    name: empty(voiceLivePeName) ? '${projectName}-voicelive-pe' : voiceLivePeName
    location: location
    subnetId: network!.outputs.peSubnetId
    // voice-live.bicep doesn't output the account ID, so resolve it
    // via the resource ID expression. resourceId() is scoped to the
    // current RG which is correct when voiceLiveMode == 'create'.
    targetResourceId: resourceId('Microsoft.CognitiveServices/accounts', voiceLiveAccountName)
    groupIds: [ 'account' ]
    dnsZoneIds: network!.outputs.aiServicesZoneIds
  }
  dependsOn: [ voiceLive ]
}

module searchPe 'modules/private-endpoint.bicep' = if (networkEnabled && features.rag) {
  name: 'searchPe'
  params: {
    name: '${projectName}-search-pe'
    location: location
    subnetId: network!.outputs.peSubnetId
    targetResourceId: search!.outputs.id
    groupIds: [ 'searchService' ]
    dnsZoneIds: [ network!.outputs.searchZoneId ]
  }
}

module ragStoragePe 'modules/private-endpoint.bicep' = if (networkEnabled && features.rag) {
  name: 'ragStoragePe'
  params: {
    name: '${projectName}-ragstorage-pe'
    location: location
    subnetId: network!.outputs.peSubnetId
    targetResourceId: ragStorage!.outputs.id
    groupIds: [ 'blob' ]
    dnsZoneIds: [ network!.outputs.blobZoneId ]
  }
}

module ragEmbeddingsPe 'modules/private-endpoint.bicep' = if (networkEnabled && features.rag && ragEmbeddingsExistingEndpoint == '') {
  name: 'ragEmbeddingsPe'
  params: {
    name: '${projectName}-embeddings-pe'
    location: location
    subnetId: network!.outputs.peSubnetId
    targetResourceId: resourceId('Microsoft.CognitiveServices/accounts', ragEmbeddingsAccountName)
    groupIds: [ 'account' ]
    dnsZoneIds: network!.outputs.aiServicesZoneIds
  }
  dependsOn: [ ragEmbeddings ]
}

module cosmosPe 'modules/private-endpoint.bicep' = if (networkEnabled && features.transcripts) {
  name: 'cosmosPe'
  params: {
    name: '${projectName}-cosmos-pe'
    location: location
    subnetId: network!.outputs.peSubnetId
    targetResourceId: resourceId('Microsoft.DocumentDB/databaseAccounts', cosmosAccountName)
    groupIds: [ 'Sql' ]
    dnsZoneIds: [ network!.outputs.cosmosZoneId ]
  }
  dependsOn: [ cosmos ]
}

// ---------- resolved values for the container app ----------
var resolvedVoiceLiveEndpoint = voiceLiveMode == 'create' ? voiceLive!.outputs.endpoint : existingVoiceLiveEndpoint
var resolvedSearchEndpoint = !empty(existingSearchEndpoint) ? existingSearchEndpoint : (features.rag ? search!.outputs.endpoint : '')
var resolvedAcrLoginServer = provisionAcr ? acr!.outputs.loginServer : acrPullRole.outputs.acrLoginServer

// Container app — depends on everything above.
module app 'modules/container-app.bicep' = if (deployApp) {
  name: 'app'
  params: {
    name: projectName
    envId: resolvedEnvId
    location: location
    acrLoginServer: resolvedAcrLoginServer
    imageName: imageName
    imageDigest: imageDigest
    appIdentityId: identity.outputs.id
    appIdentityClientId: identity.outputs.clientId
    stage: stage
    voiceLiveEndpoint: resolvedVoiceLiveEndpoint
    voiceLiveAuthMode: voiceLiveAuthMode
    voiceLiveModel: voiceLiveModel
    voiceLiveVoice: voiceLiveVoice
    msalClientId: msalClientId
    msalTenantId: msalTenantId
    msalTribeClientId: msalTribeClientId
    msalTribeTenantId: msalTribeTenantId
    customDomainName: features.customDomain ? customDomainName : ''
    customDomainCertName: features.customDomain ? customDomainCertName : ''
    personalVoiceProfileId: features.customVoice ? personalVoiceProfileId : ''
    systemPromptFile: systemPromptFile != '' ? systemPromptFile : 'agents/${projectName}/system_prompt.txt'
    knowledgeBaseFile: knowledgeBaseFile != '' ? knowledgeBaseFile : 'agents/${projectName}/knowledge_base.json'
    partnersFile: partnersFile != '' ? partnersFile : 'agents/${projectName}/partners.json'
    agentGreeting: agentGreeting
    searchEndpoint: resolvedSearchEndpoint
    searchIndexName: !empty(resolvedSearchEndpoint) ? searchIndexName : ''
    searchSemanticConfig: !empty(resolvedSearchEndpoint) ? searchSemanticConfig : ''
    cosmosEndpoint: features.transcripts ? cosmos!.outputs.endpoint : ''
    cosmosDatabaseName: features.transcripts ? cosmos!.outputs.databaseName : ''
    cosmosContainerName: features.transcripts ? cosmos!.outputs.containerName : ''
    keyVaultUri: keyVault.outputs.uri
    hasAppInsightsConnectionString: features.appInsights
    hasTranscriptSalt: features.transcripts && !empty(transcriptSalt)
    exposeOpenApiSpec: exposeOpenApiSpec
  }
  // In network mode, the image pull and downstream service
  // connections go through the PEs provisioned above. Explicit deps
  // ensure each PE + its private DNS zone group are fully wired
  // before ACA tries to pull or connect.
  // keyVaultRole is always a dep because ACA resolves keyVaultUrl
  // secrets at revision-create time using the UAMI.
  dependsOn: networkEnabled ? [
    keyVaultRole
    acrPe
    voiceLivePe
    searchPe
    ragStoragePe
    ragEmbeddingsPe
    cosmosPe
  ] : [
    keyVaultRole
  ]
}

// ---------- outputs ----------
output appUrl string = deployApp ? app!.outputs.appUrl : ''
output fqdn string = deployApp ? app!.outputs.fqdn : ''
output acrLoginServer string = resolvedAcrLoginServer
output acrName string = provisionAcr ? acr!.outputs.name : existingAcrName
output envName string = provisionEnv ? env!.outputs.name : existingContainerAppEnvName
output searchServiceName string = features.rag ? search!.outputs.name : ''
output ragStorageAccountName string = features.rag ? ragStorage!.outputs.name : ''
output ragContainerUrl string = features.rag ? ragStorage!.outputs.containerUrl : ''
output ragEmbeddingsEndpoint string = features.rag ? (ragEmbeddingsExistingEndpoint != '' ? ragEmbeddingsExistingEndpoint : ragEmbeddings!.outputs.endpoint) : ''
output ragEmbeddingsDeployment string = features.rag ? (ragEmbeddingsExistingEndpoint != '' ? ragEmbeddingsExistingDeployment : ragEmbeddings!.outputs.deploymentName) : ''
output ragEmbeddingsModel string = features.rag ? (ragEmbeddingsExistingEndpoint != '' ? ragEmbeddingModel : ragEmbeddings!.outputs.modelName) : ''
output appIdentityId string = identity.outputs.id
output voiceLiveEndpoint string = resolvedVoiceLiveEndpoint
output searchEndpoint string = resolvedSearchEndpoint
output appInsightsName string = features.appInsights ? appInsights!.outputs.name : ''
output cosmosAccountName string = features.transcripts ? cosmos!.outputs.accountName : ''
output cosmosEndpoint string = features.transcripts ? cosmos!.outputs.endpoint : ''
output cosmosDatabaseName string = features.transcripts ? cosmos!.outputs.databaseName : ''
output cosmosContainerName string = features.transcripts ? cosmos!.outputs.containerName : ''
