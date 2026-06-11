// Grants the Container App's UAMI Cosmos DB Built-in Data Contributor
// on the account, so the backend can read/write transcript docs via
// AAD without account keys.
//
// Note: Cosmos data-plane RBAC uses a custom resource type
// (Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments), NOT the
// standard Microsoft.Authorization/roleAssignments. The built-in
// "Data Contributor" role definition is fixed at GUID 0..02 per
// Cosmos account.

@description('Cosmos DB account name.')
param cosmosAccountName string

@description('UAMI principal ID (the Container App\'s identity).')
param uamiPrincipalId string

@description('Optional: object ID of the human / SP running this deploy. When set, granted Cosmos Data Contributor on the account so they can inspect data via Data Explorer in the portal (which is required because disableLocalAuth=true means keys are off). Empty to skip.')
param deployerObjectId string = ''

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

// Built-in Data Contributor: read + write on all data within the account.
// 0..01 = Data Reader, 0..02 = Data Contributor.
var dataContributorRoleId = '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'

resource uamiContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: account
  name: guid(account.id, uamiPrincipalId, 'CosmosDataContributor')
  properties: {
    principalId: uamiPrincipalId
    roleDefinitionId: dataContributorRoleId
    scope: account.id
  }
}

// Optional: also grant the deployer Data Contributor. Without this, the
// person who ran the deploy can't open Data Explorer in the portal —
// they'd hit "principal does not have required RBAC permissions on
// Microsoft.DocumentDB/databaseAccounts/readMetadata" because local auth
// (keys) is disabled. Granting the deployer matches the convention used
// by rag-storage / search-roles modules.
resource deployerContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(deployerObjectId)) {
  parent: account
  name: guid(account.id, deployerObjectId, 'CosmosDataContributorDeployer')
  properties: {
    principalId: deployerObjectId
    roleDefinitionId: dataContributorRoleId
    scope: account.id
  }
}
