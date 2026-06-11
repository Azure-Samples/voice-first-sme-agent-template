// Azure AI / Voice Live (Cognitive Services) account + realtime model
// deployment. Only deployed when features.voiceLive.mode == 'create'.

@description('Cognitive Services account name. Globally unique.')
param accountName string

@description('Azure region.')
param location string

@description('Realtime model to deploy.')
param model string = 'gpt-realtime'

// Realtime model creation currently rejects a null version in supported regions
// (Azure returns DeploymentModelNotSupported), so we pin an explicit Azure
// model release date. Bump when Azure deprecates this version.
@description('Specific model version. Pinned to a known-good value because some regions (e.g. Sweden Central) reject deployments when version is null. Set to empty string to fall back to the Azure default (do this only if you know the region accepts a null version for this model).')
param modelVersion string = '2025-08-28'

@description('Deployment capacity (units; varies by model).')
param capacity int = 1

@description('Network mode flag. When true, publicNetworkAccess is Disabled so the only data-plane path is via the private endpoint provisioned in main.bicep.')
param networkEnabled bool = false

resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: networkEnabled ? 'Disabled' : 'Enabled'
  }
}

resource voiceLiveDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiAccount
  name: model
  sku: {
    name: 'GlobalStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: model
      version: modelVersion == '' ? null : modelVersion
    }
  }
}

output endpoint string = 'https://${accountName}.cognitiveservices.azure.com'
output accountName string = aiAccount.name
output model string = model
