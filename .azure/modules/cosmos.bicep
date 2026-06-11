// Cosmos DB (NoSQL, serverless) for storing pseudonymized chat
// transcripts. One document per session, partitioned by userIdHash
// (sha256 of upn + per-deployment salt). The backend writes via MSI —
// no keys leave the account. See cosmos-roles.bicep for the RBAC.
//
// Serverless billing: pay-per-RU. For conversational volume (a few
// KB per session, write-once) typical cost is under $1/month.

@description('Cosmos DB account name. 3-44 chars, lowercase letters/digits/hyphens, globally unique.')
param accountName string

@description('Azure region.')
param location string

@description('Database name.')
param databaseName string = 'transcripts'

@description('Container name.')
param containerName string = 'sessions'

@description('Default time-to-live in seconds. -1 disables TTL. Positive int auto-expires docs N seconds after last write. Default 30 days.')
param defaultTtlSeconds int = 2592000

@description('Network mode flag. When true, publicNetworkAccess is Disabled on the account so the only data-plane path is via the private endpoint provisioned in main.bicep.')
param networkEnabled bool = false

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      { name: 'EnableServerless' }
    ]
    // Prevent key-based access. Backend uses MSI via the role
    // assignment in cosmos-roles.bicep.
    disableLocalAuth: true
    publicNetworkAccess: networkEnabled ? 'Disabled' : 'Enabled'
    minimalTlsVersion: 'Tls12'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: [ '/userIdHash' ]
        kind: 'Hash'
      }
      defaultTtl: defaultTtlSeconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [ { path: '/*' } ]
        // Don't index the turn text — saves RU on writes and we never
        // filter on it server-side (analytics queries do full reads).
        excludedPaths: [
          { path: '/turns/*' }
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

output accountId string = account.id
output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
output containerName string = container.name
