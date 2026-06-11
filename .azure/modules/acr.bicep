@description('ACR name. Must be globally unique, alphanumeric, 5-50 chars.')
param name string

@description('Azure region.')
param location string

@description('Network mode flag. When true, the ACR is provisioned with the Premium SKU (required for private endpoints) and may be locked down per `lockdown`. When false, behaves like the legacy default (Basic, publicNetworkAccess Enabled). PE itself is created out-of-band in main.bicep via the generic private-endpoint module.')
param networkEnabled bool = false

@description('Pass-2-only lockdown flag. Ignored when networkEnabled is false. When true and networkEnabled is true, sets publicNetworkAccess: Disabled. When false (pass 1), leaves ACR fully publicly accessible so `az acr build` can push images without dealing with ACR Tasks firewall edge cases.')
param lockdown bool = false

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  // Premium is required for private endpoints. SKU upgrade Basic ->
  // Premium is supported in-place (no recreate), so flipping
  // networkEnabled on a project that already deployed without it
  // doesn't lose images.
  sku: { name: networkEnabled ? 'Premium' : 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: (networkEnabled && lockdown) ? 'Disabled' : 'Enabled'
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
