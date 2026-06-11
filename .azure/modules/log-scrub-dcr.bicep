// Workspace transformation Data Collection Rule that scrubs secret-
// bearing query parameters from Container Apps console logs *before*
// they are persisted to Log Analytics.
//
// Why this exists
// ---------------
// The Voice Live WebSocket upgrade request carries the caller's bearer
// JWT as `?token=…` (legacy Cognitive Services pattern), and the
// key-auth path includes `?api-key=…`. Uvicorn access logging is
// already disabled (Tier 2.5 #6a, __PROJECT_NAME__/Dockerfile), but a future
// change — re-enabled access logs, an application logger that prints
// a request URL, an exception traceback that includes the URL —
// could re-introduce the leak. This DCR rewrites the value to
// `REDACTED` at ingest time, so even an accidental log line never
// persists the real secret to a workspace where it would be
// queryable for the retention window (30 days for this workspace).
//
// Why a workspace-transformation DCR
// ----------------------------------
// The ACA managed environment in this template uses the legacy
// `log-analytics` log destination (HTTP Data Collector API), which
// lands rows in `ContainerAppConsoleLogs_CL`. Workspace
// transformation DCRs apply to data sent directly to Log Analytics
// without an explicit ingestion DCR, which is exactly that path.
// See:
// https://learn.microsoft.com/azure/azure-monitor/essentials/data-collection-transformations-workspace
//
// Adding more tables later
// ------------------------
// A workspace can only have ONE workspace-transformation DCR. To
// scrub additional tables, add another entry to `dataFlows` below —
// do NOT create a second DCR (the second association will silently
// fail to bind).

@description('Name of the existing Log Analytics workspace to attach the transformation to.')
param logAnalyticsWorkspaceName string

@description('Full resource ID of the Log Analytics workspace (destination of the DCR).')
param logAnalyticsWorkspaceId string

@description('Azure region — must match the workspace region.')
param location string

@description('Name of the workspace transformation DCR resource.')
param dcrName string

// The actual KQL transform.
//
// Regex: `([?&])(token|api-key|ticket)=[^&\s"']+`
//   - `([?&])`           — captures the leading `?` or `&` so we can
//                          put it back in the output.
//   - `(token|api-key|ticket)`
//                        — the query-param names we treat as secret.
//                          `ticket` is the single-use WS auth ticket
//                          minted by POST /api/voice/ticket (Tier 2.5
//                          #6c). Already short-lived and single-use,
//                          but redacting it at ingest is cheap and
//                          keeps the "no credentials in URLs in logs"
//                          invariant simple.
//   - `=[^&\s"']+`       — the value: anything up to the next param
//                          separator (`&`), whitespace, or a quote
//                          character. Tight on purpose so the regex
//                          doesn't run past the URL into surrounding
//                          log text.
//
// Replacement: `\1\2=REDACTED` — keep the leading delimiter and the
// param name, replace the value with the literal string `REDACTED`.
//
// `replace_regex` on a non-matching row returns the input unchanged,
// so this is safe to apply to every row in the table.
var scrubTransformKql = 'source | extend Log_s = replace_regex(Log_s, @"([?&])(token|api-key|ticket)=[^&\\s""\']+", @"\\1\\2=REDACTED")'

resource dcr 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: dcrName
  location: location
  kind: 'WorkspaceTransforms'
  properties: {
    // `dataSources: {}` is required even for workspace transforms;
    // the schema validator rejects the DCR without it.
    dataSources: {}
    destinations: {
      logAnalytics: [
        {
          name: 'workspace'
          workspaceResourceId: logAnalyticsWorkspaceId
        }
      ]
    }
    dataFlows: [
      {
        streams: [ 'Microsoft-Table-ContainerAppConsoleLogs_CL' ]
        destinations: [ 'workspace' ]
        transformKql: scrubTransformKql
        // Input stream uses the `Microsoft-Table-<name>` form; the
        // output stream of an in-place transform on a `_CL` (custom)
        // table uses the `Custom-<name>` form. They are NOT the same
        // prefix — Azure rejects `Microsoft-Table-` as an outputStream.
        outputStream: 'Custom-ContainerAppConsoleLogs_CL'
      }
    ]
  }
}

// Reference the existing workspace so we can scope the association
// to it. The workspace itself is provisioned in `log-analytics.bicep`.
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

// Associate the DCR with the workspace. The association is what
// actually wires the transform into the ingestion path; without it
// the DCR is inert.
resource dcra 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = {
  name: 'workspace-token-scrub'
  scope: workspace
  properties: {
    dataCollectionRuleId: dcr.id
    description: 'Redact token=…, api-key=…, ticket=… query params from ACA console logs at ingest.'
  }
}

output id string = dcr.id
output name string = dcr.name
