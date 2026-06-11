// Grants the Container App's UAMI 'Cognitive Services User' on a Voice
// Live (Cognitive Services / AIServices) account so the backend can
// call the Voice Live API with a Bearer token (Microsoft Entra) instead
// of an account key.
//
// Scoped to its own module so the caller can use
// `scope: resourceGroup(<otherRg>)` to grant on an account that lives
// in a different resource group from the Container App (e.g. the
// pipeline reusing the shared `__OPENAI_ACCOUNT_NAME__` account in `__PROJECT_NAME__-rg`).
//
// Role: Cognitive Services User
// Role ID: a97b65f3-24c7-4388-baec-2e87135dc908

@description('Cognitive Services / AIServices account name (Voice Live account) in the target RG.')
param voiceLiveAccountName string

@description('UAMI principal ID to grant Cognitive Services User to.')
param uamiPrincipalId string

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: voiceLiveAccountName
}

var cogServicesUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'a97b65f3-24c7-4388-baec-2e87135dc908'
)

resource uamiCogServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, uamiPrincipalId, 'CognitiveServicesUser')
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cogServicesUserRoleId
  }
}

output accountId string = account.id
output accountName string = account.name
