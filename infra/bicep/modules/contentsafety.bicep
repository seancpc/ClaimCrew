// Content Safety:輸入過濾 + groundedness 檢核(SPEC §8,F0 起步;Phase 2)
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}

resource cs 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'cs-${baseName}'
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: { name: 'F0' }
  properties: {
    customSubDomainName: 'cs-${baseName}'
    publicNetworkAccess: 'Enabled'
  }
}

output endpoint string = cs.properties.endpoint
output accountName string = cs.name
