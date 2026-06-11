@description('Log Analytics workspace name.')
param name string

@description('Azure region.')
param location string

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

output customerId string = logs.properties.customerId
#disable-next-line outputs-should-not-contain-secrets
output primarySharedKey string = logs.listKeys().primarySharedKey
output id string = logs.id
