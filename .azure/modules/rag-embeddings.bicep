// Azure OpenAI account dedicated to RAG embeddings.
//
// Why a separate account? The voice-live cognitive services account may
// be shared across agents or already exist (mode == 'existing'). Putting
// embeddings on its own resource keeps RAG self-contained: this module
// owns the deployment AND the role assignment, so the search indexer
// can call it without us reaching across resource boundaries.
//
// What it provides:
//   - text-embedding-3-large deployment (3072 dims by default; can be
//     truncated by the embedding skill to 1536/1024/512 if you want).
//   - 'Cognitive Services OpenAI User' role granted to the AI Search
//     service's MSI, so the indexer's AzureOpenAIEmbeddingSkill and the
//     index-time vectorizer can both call /embeddings without a key.

@description('OpenAI account name. Globally unique.')
param accountName string

@description('Azure region.')
param location string

@description('Embedding model to deploy.')
param embeddingModel string = 'text-embedding-3-large'

@description('Specific model version. Empty = Azure default.')
param embeddingModelVersion string = ''

@description('Deployment capacity (units). 1 unit = 1K tokens/min for embeddings.')
param capacity int = 30

@description('Search service principal ID — granted Cognitive Services OpenAI User.')
param searchPrincipalId string

@description('Network mode flag. PR 2: PE provisioned, PNA stays Enabled (indexer ran in Standard exec env outside VNet). PR 3: PNA flips to Disabled; the AI Search indexer reaches embeddings via a Shared Private Link created by the Search module, and the Container App reaches embeddings via the PE we own.')
param networkEnabled bool = false

resource embeddings 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: location
  // 'AIServices' is the unified Azure AI Services resource. It can host
  // OpenAI model deployments (chat, embeddings, realtime) but isn't
  // subject to the Azure_OpenAI_* tenant policies (NetSec / DLP / MSI)
  // that target kind: 'OpenAI' accounts. Voice Live uses the same kind
  // for the same reason.
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName
    // Network mode (PR 3): block all public traffic. Two private
    // paths remain:
    //   1) The Container App reaches embeddings via the PE we own
    //      (`ragEmbeddingsPe` in main.bicep), resolved by private DNS.
    //   2) The AI Search indexer reaches embeddings via a Search-
    //      managed Shared Private Link (created in ai-search.bicep)
    //      once the indexer runs with executionEnvironment=private.
    publicNetworkAccess: networkEnabled ? 'Disabled' : 'Enabled'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: embeddings
  name: embeddingModel
  sku: {
    name: 'GlobalStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModel
      version: embeddingModelVersion == '' ? null : embeddingModelVersion
    }
  }
}

// Cognitive Services OpenAI User — read + invoke /embeddings.
var openAiUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
)

resource searchEmbeddingsAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: embeddings
  name: guid(embeddings.id, searchPrincipalId, 'CognitiveServicesOpenAIUser')
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: openAiUserRoleId
  }
}

output endpoint string = 'https://${accountName}.cognitiveservices.azure.com'
output accountName string = embeddings.name
output id string = embeddings.id
output deploymentName string = embeddingDeployment.name
output modelName string = embeddingModel
