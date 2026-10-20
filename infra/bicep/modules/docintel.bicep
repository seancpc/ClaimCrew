// Document Intelligence:F0 起步(500 頁/月免費,SPEC §8)
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}

resource di 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'di-${baseName}'
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: { name: 'F0' }
  properties: {
    customSubDomainName: 'di-${baseName}'
    publicNetworkAccess: 'Enabled'
  }
}

output endpoint string = di.properties.endpoint
output accountName string = di.name
