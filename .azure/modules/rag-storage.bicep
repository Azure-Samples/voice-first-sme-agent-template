// Storage account + blob container for the RAG pipeline. Users drop
// PDFs/docs in the container; the AI Search indexer picks them up.
//
// Uses managed identity end-to-end:
//   - disableLocalAuth + allowSharedKeyAccess off to satisfy tenant policy
//   - search service MSI is granted Storage Blob Data Reader so the
//     indexer can read blobs without a key

@description('Storage account name. 3-24 chars, lowercase letters/numbers, globally unique.')
param name string

@description('Azure region.')
param location string

@description('Blob container name to create (default: rag-documents).')
param containerName string = 'rag-documents'

@description('Principal ID of the AI Search service MSI to grant Storage Blob Data Reader.')
param searchPrincipalId string

@description('Object ID of the user (or service principal) running the deploy. When set, granted Storage Blob Data Contributor on this account so they can upload PDFs via Azure Storage Explorer / portal without needing extra setup. Empty string skips the role assignment.')
param deployerObjectId string = ''

@description('Principal type for deployerObjectId. "User" for interactive deploys, "ServicePrincipal" for CI/CD.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerPrincipalType string = 'User'

@description('Network mode flag. When true, the storage account stays publicly accessible (publicNetworkAccess: Enabled — Azure does not let us combine PNA Disabled with IP rules) but the network ACL set defaultAction: Deny, plus `bypass: AzureServices` so the AI Search indexer (a trusted Microsoft service) and the deployer IP rule below are the only paths in.')
param networkEnabled bool = false

@description('Deployer public IP (IPv4) added to the storage network ACL in network mode so the deployer can upload PDFs via Storage Explorer / portal / AzCopy.')
param deployerIpAddress string = ''

@description('Additional IPv4 addresses (or CIDR ranges) allowed to reach this storage account in network mode. Use this to allow-list team-member workstation IPs so the team can upload documents via Storage Explorer / Portal / AzCopy without each user needing to be the deployer. Survives redeploys (out-of-band `az storage account network-rule add` would be wiped each time bicep runs).')
param additionalAllowedIpRules array = []

var deployerIpRules = empty(deployerIpAddress) ? [] : [
  { value: deployerIpAddress, action: 'Allow' }
]
var teamIpRules = [for ip in additionalAllowedIpRules: {
  value: ip
  action: 'Allow'
}]

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    // Tenant policy disallows shared-key access. Indexer reads via MSI.
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: networkEnabled ? {
      defaultAction: 'Deny'
      // 'AzureServices' allows the AI Search indexer (a trusted
      // Microsoft service) to reach blobs when the Search MSI has
      // the right RBAC role granted on this account. Logging +
      // Metrics keep diagnostic pipelines working.
      bypass: 'AzureServices, Logging, Metrics'
      ipRules: concat(deployerIpRules, teamIpRules)
    } : {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    // Soft delete is required for the indexer's
    // NativeBlobSoftDeleteDeletionDetectionPolicy to remove docs from
    // the index when they're deleted from the container.
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

// Grant the AI Search service MSI Storage Blob Data Reader on the
// storage account so its indexer can list+read blobs via AAD.
var blobDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
)

resource searchBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, searchPrincipalId, 'StorageBlobDataReader')
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobDataReaderRoleId
  }
}

// Storage Blob Data Contributor — needed because shared-key access is
// disabled on this account, so the user running deploy needs an RBAC
// data-plane role to upload PDFs via Storage Explorer / portal /
// AzCopy. Without this they hit "This request is not authorized to
// perform this operation using this permission" the moment they try
// to drop a file into rag-documents.
var blobDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource deployerBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployerObjectId != '') {
  scope: storage
  name: guid(storage.id, deployerObjectId, 'StorageBlobDataContributor')
  properties: {
    principalId: deployerObjectId
    principalType: deployerPrincipalType
    roleDefinitionId: blobDataContributorRoleId
  }
}

output id string = storage.id
output name string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output containerName string = container.name
output containerUrl string = '${storage.properties.primaryEndpoints.blob}${container.name}'
