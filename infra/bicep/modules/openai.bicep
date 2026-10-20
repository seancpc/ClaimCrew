// Azure OpenAI:GPT-4o + mini 分層路由(SPEC §5.3);模型版本釘選(LLM 規範第 1 條)
// 2026-09-30 首次 what-if 實證:gpt-4o 2024-08-06 / 4o-mini 2024-07-18 皆 Deprecating,
// preflight 回 ServiceModelDeprecating;且本訂閱 GlobalStandard 配額為 0、Standard 有 50K。
// 故主模型改 gpt-4o 2024-11-20(Legacy,退役 2027-04-14)+ Standard。
// 2026-10-02 SPEC-k(ADR-007 實測):list-models 中 Standard 可部署且非 Deprecating 的文字 mini 僅 gpt-4.1-mini 2025-04-14
// (Legacy,與主模型同級;Standard 配額 200K);gpt-5-mini / 5.4-mini 僅 GlobalStandard/DataZoneStandard 且為推理模型,留作第二臂
param baseName string
param location string
@description('每個部署的 TPM 配額(千 tokens/分);S0 計費,F0 敘事外之唯一付費項')
param modelCapacity int = 50  // 千 TPM;eastus2 Standard gpt-4o 配額上限 50K(2026-10-01 全量首跑 capacity 10 → 429 全錯)
@description('部署 SKU:本訂閱 Standard 有配額、GlobalStandard 為 0(2026-09-30 az cognitiveservices usage list)')
param deploymentSku string = 'Standard'
@description('embedding 部署 TPM(千 tokens/分);eastus2 Standard text-embedding-3-small 配額 350K(2026-09-30 實測)')
param embeddingCapacity int = 100
@description('是否部署 mini 分層模型(ADR-007 實測臂);2026-10-02 開啟')
param enableMiniModel bool = true
@description('mini 分層模型名稱與版本;部署名 = 名稱,runner 以 --extraction-deployment gpt-4.1-mini 路由(gpt-4o-mini 2024-07-18 已 Deprecating)')
param miniModelName string = 'gpt-4.1-mini'
param miniModelVersion string = '2025-04-14'
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}

resource aoai 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'oai-${baseName}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'oai-${baseName}'
    publicNetworkAccess: 'Enabled'
  }
}

resource gpt4o 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: aoai
  name: 'gpt-4o'
  sku: { name: deploymentSku, capacity: modelCapacity }
  properties: {
    versionUpgradeOption: 'NoAutoUpgrade' // 基準綁定模型版本,禁止 Azure 自動升版(pre-mortem 2026-10-01)
    model: { format: 'OpenAI', name: 'gpt-4o', version: '2024-11-20' }
  }
}

// 條款 hybrid 檢索的向量欄位(SPEC §5.3 Policy Agent / ADR-005);版本 1 GA、退役 2028-02-09(eastus2 實測)
resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: aoai
  name: 'text-embedding-3-small'
  dependsOn: [gpt4o] // deployments 需序列建立
  sku: { name: deploymentSku, capacity: embeddingCapacity }
  properties: {
    versionUpgradeOption: 'NoAutoUpgrade' // 基準綁定模型版本,禁止 Azure 自動升版(pre-mortem 2026-10-01)
    model: { format: 'OpenAI', name: 'text-embedding-3-small', version: '1' }
  }
}

resource gptmini 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = if (enableMiniModel) {
  parent: aoai
  name: miniModelName
  dependsOn: [embedding] // deployments 需序列建立
  sku: { name: deploymentSku, capacity: modelCapacity }
  properties: {
    versionUpgradeOption: 'NoAutoUpgrade' // 基準綁定模型版本,禁止 Azure 自動升版(pre-mortem 2026-10-01)
    model: { format: 'OpenAI', name: miniModelName, version: miniModelVersion }
  }
}

output endpoint string = aoai.properties.endpoint
output accountName string = aoai.name
