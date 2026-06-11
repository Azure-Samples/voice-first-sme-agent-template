// Placeholder: Azure AI Search service for RAG.
// Only deployed when features.rag is true. Wire-up to backend is TODO.

@description('Search service name. Globally unique, 2-60 chars, lowercase.')
param name string

@description('Azure region.')
param location string

@description('SKU: free, basic, standard, ...')
param sku string = 'basic'

@description('Network mode flag. When true, the Search service stays publicly accessible BUT the firewall is restricted to the supplied deployer IP. (PNA: enabled + ipRules) — required during indexer configuration because deploy.js PUTs the indexer / datasource / skillset via the public REST endpoint from the deployer\'s machine. After configureSearchIndexer runs, deploy.js re-deploys with searchLockdown=true to flip PNA to Disabled.')
param networkEnabled bool = false

@description('Deployer public IP (IPv4) added to the firewall when networkEnabled is true. Empty disables the IP rule, which will lock the deployer out of the data plane.')
param deployerIpAddress string = ''

@description('PR 3 pass-2 flag: when true, flip publicNetworkAccess to Disabled. The Container App keeps reaching Search via its private endpoint. Indexer setup (which needs the deployer\'s REST access) must have already run.')
param searchLockdown bool = false

// Standard SKU is required when networkEnabled is true because Shared
// Private Links + indexer executionEnvironment=private are not
// supported on Basic. NOTE: SKU change on an existing service is NOT
// supported in-place by ARM — toggling network mode on a previously-
// deployed Basic service requires deleting the search service first
// (`az search service delete`) and re-running ./deploy.
var effectiveSku = networkEnabled ? 'standard' : sku

resource search 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: name
  location: location
  sku: { name: effectiveSku }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: searchLockdown ? 'disabled' : 'enabled'
    semanticSearch: 'free'
    // Microsoft tenant policy (Azure_AISearch_AuthN_Use_Managed_Service_Identity)
    // requires disabling local (API key) auth. Backend must use AAD via the
    // Container App's UAMI; grant it 'Search Index Data Reader' on this service.
    disableLocalAuth: true
    authOptions: null
    // Firewall: in network mode (during pass 1 + indexer-config window),
    // restrict the public endpoint to the deployer's IP so anonymous
    // tenants on the internet are rejected while the wizard can still
    // PUT the indexer / index / skillset / datasource from outside the
    // VNet. The Container App reaches Search via the private endpoint,
    // not the firewalled public endpoint, so this rule only affects
    // the deployer path. Once searchLockdown=true, networkRuleSet is
    // moot (PNA=Disabled rejects all public traffic regardless).
    networkRuleSet: (networkEnabled && !searchLockdown && !empty(deployerIpAddress)) ? {
      ipRules: [
        { value: deployerIpAddress }
      ]
      bypass: 'AzurePortal'
    } : null
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
output principalId string = search.identity.principalId
