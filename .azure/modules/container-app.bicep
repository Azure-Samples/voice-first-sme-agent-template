@description('Container App name.')
param name string

@description('Existing Container App Environment ID.')
param envId string

@description('Azure region.')
param location string

@description('ACR login server.')
param acrLoginServer string

@description('Image reference appended to the ACR login server. Accepts either tag form ("agent-joe:0.1.0-abc123") or digest form ("agent-joe@sha256:..."). deploy.js prefers the digest form so Container App revisions are pinned to immutable images for rollback.')
param imageName string

@description('Resolved image digest ("sha256:..."), surfaced to the container as IMAGE_DIGEST for /api/health. Empty when the caller cannot resolve a digest.')
param imageDigest string = ''

@description('User-assigned managed identity ID for ACR pull.')
param appIdentityId string

@description('UAMI client ID. Set as AZURE_CLIENT_ID so DefaultAzureCredential picks the right identity.')
param appIdentityClientId string = ''

@description('Stage label (dev / prod).')
param stage string = 'dev'

@description('Voice Live endpoint.')
param voiceLiveEndpoint string

@secure()
@description('Voice Live API key. Required when voiceLiveAuthMode=key. Safe to pass empty/dummy when voiceLiveAuthMode=mi. Deprecated: value now flows via Key Vault; this param is retained so existing callers keep compiling but is no longer read.')
param voiceLiveApiKey string = ''

@description('Voice Live auth mode. "key" or "mi". Surfaced to the backend as AZURE_VOICELIVE_AUTH_MODE.')
param voiceLiveAuthMode string = 'key'

@description('Voice Live model name.')
param voiceLiveModel string

@description('Standard Azure voice used for the live Voice Live session and for pre-generated filler/nudge/greeting audio. Format: <locale>-<name>:<model> (e.g. en-US-Andrew:DragonHDLatestNeural). Surfaced to the backend as AZURE_VOICELIVE_VOICE and to the frontend via /api/voice-config.')
param voiceLiveVoice string = 'en-US-Andrew:DragonHDLatestNeural'

@description('MSAL client ID.')
param msalClientId string

@description('MSAL tenant ID.')
param msalTenantId string

@description('Optional second MSAL client ID (e.g. for a partner / tribe tenant). Empty to skip.')
param msalTribeClientId string = ''

@description('Optional second MSAL tenant ID (e.g. for a partner / tribe tenant). Empty to skip.')
param msalTribeTenantId string = ''

@description('Custom domain name. Empty to skip.')
param customDomainName string = ''

@description('Existing managed certificate name (in the env) for the custom domain. Empty to skip.')
param customDomainCertName string = ''

@description('Azure Personal Voice Speaker Profile ID. Empty (the default) keeps the standard voice. When non-empty, surfaced to the backend as AZURE_PERSONAL_VOICE_PROFILE_ID; /api/voice-config then flips the frontend onto the azure-personal voice schema.')
param personalVoiceProfileId string = ''

@description('Optional path inside container to the system prompt file.')
param systemPromptFile string = ''

@description('Optional path inside container to the knowledge base JSON file.')
param knowledgeBaseFile string = ''

@description('Optional path inside container to the partners registry JSON file.')
param partnersFile string = ''

@description('First line the agent says. Read by the backend at startup.')
param agentGreeting string = ''

@description('AI Search endpoint (only when RAG is enabled).')
param searchEndpoint string = ''

@description('AI Search index name (only when RAG is enabled).')
param searchIndexName string = ''

@description('AI Search semantic config name (only when RAG is enabled).')
param searchSemanticConfig string = ''

@description('Application Insights connection string. Empty disables backend telemetry. Deprecated: value now flows via Key Vault — set hasAppInsightsConnectionString=true when the KV secret exists.')
@secure()
param appInsightsConnectionString string = ''

@description('Cosmos DB account endpoint (only when transcripts feature is enabled).')
param cosmosEndpoint string = ''

@description('Cosmos DB database name for transcripts.')
param cosmosDatabaseName string = ''

@description('Cosmos DB container name for transcripts.')
param cosmosContainerName string = ''

@description('Key Vault URI (e.g. https://myvault.vault.azure.net/). Secrets are pulled from this vault using the Container App UAMI.')
param keyVaultUri string

@description('Secret name in Key Vault for the Voice Live API key.')
param voiceLiveApiKeySecretName string = 'voicelive-api-key'

@description('Secret name in Key Vault for the Application Insights connection string.')
param appInsightsConnectionStringSecretName string = 'appinsights-connection-string'

@description('Secret name in Key Vault for the transcript salt.')
param transcriptSaltSecretName string = 'transcript-salt'

@description('True when the transcript-salt secret is present in Key Vault (drives the conditional secret/env var wiring).')
param hasTranscriptSalt bool = false

@description('True when the appinsights-connection-string secret is present in Key Vault.')
param hasAppInsightsConnectionString bool = false

@description('Expose the OpenAPI JSON spec at /openapi.json (independent of the Swagger UI). Enable in deployments onboarded to URSA Web Scanner.')
param exposeOpenApiSpec bool = false

@secure()
@description('Per-deployment salt used to hash user identifiers before persisting transcripts. Deprecated: value now flows via Key Vault; this param is retained so existing callers (deploy.js, pipeline) keep compiling but is no longer read.')
param transcriptSalt string = ''

resource env 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: last(split(envId, '/'))
}

resource existingCert 'Microsoft.App/managedEnvironments/managedCertificates@2024-03-01' existing = if (customDomainCertName != '') {
  parent: env
  name: customDomainCertName
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: envId
    configuration: {
      // Sticky sessions (set on ingress below) require single revision
      // mode; ARM rejects affinity when this is unset/Multiple
      // (ContainerAppInvalidIngressStickySessionRevisionMode).
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        // Sticky sessions: required for the single-use WS ticket exchange
        // (app/voice_tickets.py). The POST /api/voice/ticket and the
        // subsequent WS open must land on the same replica so the
        // in-process ticket store can redeem the ticket. ACA writes a
        // cookie on first response; the browser replays it on the WS
        // handshake (same origin).
        stickySessions: {
          affinity: 'sticky'
        }
        customDomains: customDomainName != '' && customDomainCertName != '' ? [
          {
            name: customDomainName
            certificateId: existingCert.id
            bindingType: 'SniEnabled'
          }
        ] : []
      }
      registries: [
        {
          server: acrLoginServer
          identity: appIdentityId
        }
      ]
      secrets: concat(
        // Only include the voice-live API key secret when we're actually using
        // key auth. In MI mode we'd reference a non-existent KV secret and
        // ARM rejects the revision; omit the secret entry entirely instead.
        voiceLiveAuthMode == 'key' ? [
          {
            name: 'voicelive-api-key'
            keyVaultUrl: '${keyVaultUri}secrets/${voiceLiveApiKeySecretName}'
            identity: appIdentityId
          }
        ] : [],
        hasAppInsightsConnectionString ? [
          {
            name: 'appinsights-connection-string'
            keyVaultUrl: '${keyVaultUri}secrets/${appInsightsConnectionStringSecretName}'
            identity: appIdentityId
          }
        ] : [],
        hasTranscriptSalt ? [
          {
            name: 'transcript-salt'
            keyVaultUrl: '${keyVaultUri}secrets/${transcriptSaltSecretName}'
            identity: appIdentityId
          }
        ] : []
      )
    }
    template: {
      containers: [
        {
          name: name
          image: '${acrLoginServer}/${imageName}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(
            [
              { name: 'STAGE', value: stage }
              { name: 'PROJECT_NAME', value: name }
              { name: 'AZURE_VOICELIVE_ENDPOINT', value: voiceLiveEndpoint }
              { name: 'AZURE_VOICELIVE_AUTH_MODE', value: voiceLiveAuthMode }
              { name: 'AZURE_CLIENT_ID', value: appIdentityClientId }
              { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
              { name: 'AZURE_SEARCH_INDEX_NAME', value: searchIndexName }
              { name: 'AZURE_SEARCH_SEMANTIC_CONFIG', value: searchSemanticConfig }
              { name: 'AZURE_VOICELIVE_MODEL', value: voiceLiveModel }
              { name: 'AZURE_VOICELIVE_VOICE', value: voiceLiveVoice }
              { name: 'MSAL_CLIENT_ID', value: msalClientId }
              { name: 'MSAL_TENANT_ID', value: msalTenantId }
              { name: 'MSAL_TRIBE_CLIENT_ID', value: msalTribeClientId }
              { name: 'MSAL_TRIBE_TENANT_ID', value: msalTribeTenantId }
              { name: 'SYSTEM_PROMPT_FILE', value: systemPromptFile }
              { name: 'KNOWLEDGE_BASE_FILE', value: knowledgeBaseFile }
              { name: 'PARTNERS_FILE', value: partnersFile }
              { name: 'AGENT_GREETING', value: agentGreeting }
              { name: 'AZURE_COSMOS_ENDPOINT', value: cosmosEndpoint }
              { name: 'AZURE_COSMOS_DATABASE', value: cosmosDatabaseName }
              { name: 'AZURE_COSMOS_CONTAINER', value: cosmosContainerName }
              { name: 'IMAGE_DIGEST', value: imageDigest }
              { name: 'EXPOSE_OPENAPI_SPEC', value: exposeOpenApiSpec ? 'true' : 'false' }
            ],
            // Only inject AZURE_VOICELIVE_API_KEY when the matching secret
            // exists (i.e. when authMode=key). MI mode pulls a bearer token
            // via DefaultAzureCredential instead and doesn't need the var.
            voiceLiveAuthMode == 'key' ? [
              { name: 'AZURE_VOICELIVE_API_KEY', secretRef: 'voicelive-api-key' }
            ] : [],
            personalVoiceProfileId != '' ? [
              { name: 'AZURE_PERSONAL_VOICE_PROFILE_ID', value: personalVoiceProfileId }
            ] : [],
            hasAppInsightsConnectionString ? [
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
            ] : [],
            hasTranscriptSalt ? [
              { name: 'TRANSCRIPT_SALT_BASE64', secretRef: 'transcript-salt' }
            ] : []
          )
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output appUrl string = customDomainName != '' ? 'https://${customDomainName}' : 'https://${app.properties.configuration.ingress.fqdn}'
