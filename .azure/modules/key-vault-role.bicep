// Grants the Container App's UAMI permission to read secrets from
// Key Vault, using the modern RBAC role (Key Vault Secrets User).
// ACA's keyVaultUrl secret resolver authenticates with the UAMI and
// requires this role.

@description('Key Vault name in the target RG.')
param keyVaultName string

@description('UAMI principal ID to grant Key Vault Secrets User to.')
param uamiPrincipalId string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Built-in role: Key Vault Secrets User
// https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles#key-vault-secrets-user
var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource secretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, uamiPrincipalId, 'KeyVaultSecretsUser')
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

output roleAssignmentId string = secretsUserAssignment.id
