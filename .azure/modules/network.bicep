// VNet + private DNS foundation for optional private-egress mode.
//
// Provides:
//   - A VNet with two subnets:
//       * aca-subnet (delegated to Microsoft.App/environments) — host
//         the Container Apps managed environment's infrastructure
//         VMs. Min /27 for workload-profiles envs.
//       * pe-subnet — host the private-endpoint NICs for ACR, AI
//         Search, Storage, Cognitive Services (Voice Live +
//         embeddings), and Cosmos. Private-endpoint network
//         policies are disabled here (the subnet must allow PE NICs
//         to be created).
//   - Private DNS zones for every privatelink-capable resource we
//     hang off the VNet, plus a VNet link on each zone with
//     `registrationEnabled: false` (we don't want VMs auto-registering
//     here — we manage records via the PE's dnsZoneGroup).
//
// Subnets are declared as sibling resources with `dependsOn` to
// serialize creation (parallel subnet creates inside the same VNet
// race and one randomly fails with a "subnet conflict" error — this
// is a well-known Bicep / ARM gotcha).

@description('VNet name.')
param vnetName string

@description('Azure region.')
param location string

@description('VNet address space CIDR.')
param addressSpace string = '10.20.0.0/16'

@description('Subnet for the ACA workload-profiles environment. Min /27. Delegated to Microsoft.App/environments.')
param acaSubnetCidr string = '10.20.0.0/27'

@description('Subnet for private endpoint NICs.')
param peSubnetCidr string = '10.20.1.0/24'

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [ addressSpace ]
    }
  }
}

resource acaSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: 'aca-subnet'
  properties: {
    addressPrefix: acaSubnetCidr
    delegations: [
      {
        name: 'aca-env-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: 'pe-subnet'
  properties: {
    addressPrefix: peSubnetCidr
    // PE NICs cannot be created when subnet network policies are on.
    privateEndpointNetworkPolicies: 'Disabled'
  }
  // Serialize subnet creation. Parallel creates inside the same VNet
  // race and one randomly fails with EtagMismatch.
  dependsOn: [ acaSubnet ]
}

// Private DNS zones. Listed in a stable order; outputs reference by
// index. AIServices accounts (kind: 'AIServices', used for Voice Live
// + embeddings) may register A records under any of the three cog /
// openai / services.ai zones depending on the SDK call path — link
// all three to be safe.
var zoneNames = [
  'privatelink.azurecr.io'                  // 0 — ACR
  'privatelink.search.windows.net'          // 1 — AI Search
  'privatelink.blob.core.windows.net'       // 2 — Storage blob
  'privatelink.cognitiveservices.azure.com' // 3 — AIServices (legacy zone)
  'privatelink.openai.azure.com'            // 4 — AIServices (OpenAI compat zone)
  'privatelink.services.ai.azure.com'       // 5 — AIServices (Foundry / forward compat)
  'privatelink.documents.azure.com'         // 6 — Cosmos SQL
]

resource dnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for z in zoneNames: {
  name: z
  location: 'global'
}]

resource zoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (z, i) in zoneNames: {
  name: 'link-${vnetName}'
  parent: dnsZones[i]
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}]

output vnetId string = vnet.id
output acaSubnetId string = acaSubnet.id
output peSubnetId string = peSubnet.id

output acrZoneId string = dnsZones[0].id
output searchZoneId string = dnsZones[1].id
output blobZoneId string = dnsZones[2].id
output cogServicesZoneId string = dnsZones[3].id
output openAiZoneId string = dnsZones[4].id
output aiServicesZoneId string = dnsZones[5].id
output cosmosZoneId string = dnsZones[6].id

// Convenience bundles for the AIServices PE (Voice Live, embeddings)
// which we want to register under all three Foundry-family zones.
output aiServicesZoneIds array = [
  dnsZones[3].id
  dnsZones[4].id
  dnsZones[5].id
]
