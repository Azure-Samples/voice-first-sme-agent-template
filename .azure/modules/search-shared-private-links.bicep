// Search-managed Shared Private Links (SPL).
//
// PR 3 closes the public-egress holes left after PR 2: AI Search keeps
// PNA=Enabled+IP-rule during the indexer-config window, but its indexer
// still needs to reach Storage + Embeddings privately. SPLs are
// Microsoft.Search-owned private endpoints that the indexer's
// "private" execution environment can use to call those resources
// even when their public network access is locked down.
//
// This module is wired AFTER Search, ragStorage, and ragEmbeddings
// are deployed so the dependency graph stays acyclic. It uses the
// `existing` keyword to attach SPL child resources to the already-
// provisioned Search service.
//
// Lifecycle: the SPL is created in "Pending" state on the target
// account. deploy.js approves the pending connection via
// `az network private-endpoint-connection approve` and polls until
// the SPL reports provisioningState=Succeeded + status=Approved
// before running the indexer.

@description('Name of the (existing) AI Search service.')
param searchServiceName string

@description('Storage account resource ID for groupId=blob SPL. Empty = skip.')
param storageAccountId string = ''

@description('Embeddings (AIServices/OpenAI) account resource ID for groupId=openai_account SPL. Empty = skip.')
param embeddingsAccountId string = ''

// Unique marker so deploy.js can identify the connections originating
// from this Search service on the target side (Storage / Embeddings)
// when filtering pending PE connections to approve. Used as the SPL
// requestMessage which surfaces in the privateEndpointConnections's
// privateLinkServiceConnectionState.description.
var splMarker = 'agent-template-spl-${searchServiceName}'

resource search 'Microsoft.Search/searchServices@2024-03-01-preview' existing = {
  name: searchServiceName
}

resource splStorage 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2024-03-01-preview' = if (!empty(storageAccountId)) {
  parent: search
  name: 'spl-rag-storage'
  properties: {
    privateLinkResourceId: storageAccountId
    groupId: 'blob'
    requestMessage: '${splMarker}/blob'
  }
}

resource splEmbeddings 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2024-03-01-preview' = if (!empty(embeddingsAccountId)) {
  // Embeddings (Microsoft.CognitiveServices/accounts of kind AIServices
  // that hosts an OpenAI model deployment): the documented SPL groupId
  // for the OpenAI sub-endpoint is 'openai_account'. The same value
  // is used for both kind: 'OpenAI' and kind: 'AIServices' accounts
  // when the target traffic is OpenAI model invocations.
  //
  // Must be serialized after splStorage. Azure Search's REST API only
  // accepts one sharedPrivateLinkResources PUT at a time on a given
  // service; concurrent PUTs return ConflictError ("There was a
  // conflicting update") and the second resource is silently dropped.
  parent: search
  name: 'spl-rag-embeddings'
  properties: {
    privateLinkResourceId: embeddingsAccountId
    groupId: 'openai_account'
    requestMessage: '${splMarker}/openai_account'
  }
  dependsOn: [
    splStorage
  ]
}

output splMarker string = splMarker
output splStorageCreated bool = !empty(storageAccountId)
output splEmbeddingsCreated bool = !empty(embeddingsAccountId)
