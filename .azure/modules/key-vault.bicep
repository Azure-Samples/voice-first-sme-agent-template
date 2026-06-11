// Azure Key Vault for application secrets.
//
// ACA Container App references these secrets via `keyVaultUrl` so the
// secret values never leave Key Vault — they're fetched by the
// platform at revision-create time using the Container App's UAMI.
// See key-vault-role.bicep for the role assignment that grants the
// UAMI read access.
//
// RBAC is enabled (not legacy access policies) so the platform team's
// standard Entra-group permissions apply.

@description('Key Vault name. Must be globally unique, 3-24 chars, alphanumeric + dashes, start with a letter.')
@minLength(3)
@maxLength(24)
param name string

@description('Azure region.')
param location string

@secure()
@description('Voice Live API key. Empty value skips secret creation (e.g. when voiceLiveAuthMode=mi).')
param voiceLiveApiKey string = ''

@secure()
@description('Application Insights connection string. Empty value skips secret creation.')
param appInsightsConnectionString string = ''

@secure()
@description('Per-deployment salt used to hash transcript user IDs. Empty value skips secret creation.')
param transcriptSalt string = ''

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource voiceLiveApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(voiceLiveApiKey)) {
  parent: kv
  name: 'voicelive-api-key'
  properties: {
    value: voiceLiveApiKey
    contentType: 'text/plain'
  }
}

resource appInsightsConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(appInsightsConnectionString)) {
  parent: kv
  name: 'appinsights-connection-string'
  properties: {
    value: appInsightsConnectionString
    contentType: 'text/plain'
  }
}

resource transcriptSaltSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(transcriptSalt)) {
  parent: kv
  name: 'transcript-salt'
  properties: {
    value: transcriptSalt
    contentType: 'text/plain'
  }
}

output id string = kv.id
output name string = kv.name
output uri string = kv.properties.vaultUri
