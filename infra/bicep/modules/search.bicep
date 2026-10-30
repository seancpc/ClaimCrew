// AI Search:Free tier 起步(3 indexes/50MB;展示高峰可改 basic,SPEC §8 ADR)
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'srch-${baseName}'
  location: location
  tags: tags
  sku: { name: 'free' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
  }
}

output endpoint string = 'https://${search.name}.search.windows.net'
