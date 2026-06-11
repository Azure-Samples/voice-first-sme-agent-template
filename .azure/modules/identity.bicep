// User-assigned managed identity used by the Container App for ACR
// pulls, OpenAI / Search / Cosmos data-plane access, etc.
//
// AcrPull is granted by a separate module (acr-pull-role.bicep) so the
// role assignment can be scoped to a different RG when reusing a shared
// ACR.

@description('Identity name.')
param name string

@description('Azure region.')
param location string

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
}

output id string = appIdentity.id
output principalId string = appIdentity.properties.principalId
output clientId string = appIdentity.properties.clientId
