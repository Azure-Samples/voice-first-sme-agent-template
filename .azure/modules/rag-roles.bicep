// Grants the Container App's UAMI 'Search Index Data Reader' on the AI
// Search service so the backend can query the index via AAD.

@description('AI Search service name.')
param searchServiceName string

@description('UAMI principal ID (the Container App\'s identity).')
param uamiPrincipalId string

resource search 'Microsoft.Search/searchServices@2024-03-01-preview' existing = {
  name: searchServiceName
}

// Search Index Data Reader — read-only access to indexes.
var indexDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '1407120a-92aa-4202-b7e9-c0e197c71c8f'
)

resource uamiIndexReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, uamiPrincipalId, 'SearchIndexDataReader')
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: indexDataReaderRoleId
  }
}
