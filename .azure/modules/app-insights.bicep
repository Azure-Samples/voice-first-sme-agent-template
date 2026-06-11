// Workspace-based Application Insights instance for backend telemetry.
// Provisioned when features.appInsights is true in main.bicep. The
// connection string is plumbed via Key Vault into the Container App
// as APPLICATIONINSIGHTS_CONNECTION_STRING (so the
// azure-monitor-opentelemetry distro can pick it up once the backend
// calls configure_azure_monitor()) and into the ACA env as the
// daprAIConnectionString (so any Dapr-routed traffic is traced
// out-of-the-box).

@description('Application Insights resource name.')
param name string

@description('Azure region.')
param location string

@description('Resource ID of the existing Log Analytics workspace to back this AI instance.')
param workspaceId string

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: name
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspaceId
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

@description('Connection string used by the backend OTel distro.')
#disable-next-line outputs-should-not-contain-secrets
output connectionString string = appInsights.properties.ConnectionString

output id string = appInsights.id
output name string = appInsights.name
