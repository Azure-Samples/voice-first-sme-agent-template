// AcrPull role assignment for a UAMI on a Container Registry. Lives in
// its own module so the caller can use `scope: resourceGroup(...)` to
// grant on an ACR in a different resource group (e.g. when the
// container app reuses a shared registry owned by a platform team).

@description('ACR name in the target RG.')
param acrName string

@description('UAMI principal ID to grant AcrPull to.')
param uamiPrincipalId string

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, uamiPrincipalId, 'AcrPull')
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

output acrId string = acr.id
output acrLoginServer string = acr.properties.loginServer
