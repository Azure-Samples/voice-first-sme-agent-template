@description('Container App Environment name.')
param name string

@description('Azure region.')
param location string

@description('Log Analytics customer ID.')
param logAnalyticsCustomerId string

@secure()
@description('Log Analytics primary shared key.')
param logAnalyticsSharedKey string

@description('Application Insights connection string. Empty disables AI-side telemetry forwarding for this env.')
@secure()
param appInsightsConnectionString string = ''

@description('VNet subnet ID for ACA infrastructure (workload-profiles env egress). When non-empty, switches the env to workload profiles + vnetConfiguration so outbound traffic routes through this subnet. Public ingress is preserved (internal: false). When empty, deploys a Consumption-only env without VNet integration (current default behavior).')
param vnetSubnetId string = ''

var hasVnet = !empty(vnetSubnetId)

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    daprAIConnectionString: empty(appInsightsConnectionString) ? null : appInsightsConnectionString
    // Workload-profile env with VNet integration. internal: false
    // preserves the existing public ingress behavior (Container App
    // FQDN stays *.azurecontainerapps.io and is publicly
    // resolvable); only outbound egress is routed through the VNet.
    vnetConfiguration: hasVnet ? {
      internal: false
      infrastructureSubnetId: vnetSubnetId
    } : null
    // workloadProfiles is required when vnetConfiguration is set —
    // Consumption-only is the minimum (pay-per-use, no dedicated SKU
    // cost).
    workloadProfiles: hasVnet ? [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ] : null
  }
}

output id string = env.id
output name string = env.name
