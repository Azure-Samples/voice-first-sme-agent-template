// Generic private endpoint + privateDnsZoneGroup wrapper.
//
// Use this from main.bicep once per resource you want to attach a PE
// to. The PE NIC is created in the pe-subnet, and (if dnsZoneIds is
// non-empty) the privateDnsZoneGroup registers A records under every
// supplied zone — useful for resources like AIServices accounts that
// resolve through more than one privatelink zone.
//
// The `location` parameter must be the VNet/subnet region, NOT the
// target resource's region. PEs live in the VNet and the subnet
// region is what determines PE location.

@description('Private endpoint name.')
param name string

@description('Azure region of the VNet / subnet (NOT the target resource\'s region).')
param location string

@description('Subnet ID where the PE NIC will be created.')
param subnetId string

@description('Full resource ID of the target resource (e.g. acr.id, search.id).')
param targetResourceId string

@description('Group IDs for the private link service connection. One per kind of resource; e.g. ["registry"] for ACR, ["blob"] for storage, ["account"] for AIServices, ["Sql"] for Cosmos SQL.')
param groupIds array

@description('Private DNS zone IDs to register A records under. Empty skips DNS wiring (caller is on the hook for resolution).')
param dnsZoneIds array = []

resource pe 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: name
  location: location
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${name}-conn'
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: groupIds
        }
      }
    ]
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (!empty(dnsZoneIds)) {
  parent: pe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zoneId, i) in dnsZoneIds: {
      name: 'config${i}'
      properties: {
        privateDnsZoneId: zoneId
      }
    }]
  }
}

output id string = pe.id
output name string = pe.name
