// Foundry 帳戶 + 專案 + 模型(SPEC-n 探針 2026-10-02;SPEC-o 2026-10-03 搬 eastus 並加 gpt-4o 判定/修補模型)
// - 2026-10-03:eastus2 的 Standard gpt-4o 50K 已被 oai-${baseName} 用滿;eastus 50K 未用且 11-20 版 Legacy 可部署(list-models 實證)
//   → 帳戶改建 eastus(main.bicep foundryLocation),舊 eastus2 帳戶由 runbook 4.15 delete + purge
// - 帳戶 kind AIServices + allowProjectManagement,與既有 kind OpenAI 的 oai-${baseName} 分開(Agent 引用既有帳戶部署未經驗證,列為後續探針)
// - 探針模型 gpt-4.1-mini 2025-04-14 / Standard(eastus2 配額 200K,oai 帳戶已用 50;gpt-4o 配額 50K 已被主部署用滿)
// - 無固定月費:帳戶/專案不計費,只付 token(官方價目頁 2026-10-02 查證);Basic setup 對話資料存微軟託管儲存(全合成零 PHI,可接受)
// - RBAC(Foundry User,舊名 Azure AI User)由 runbook 以角色名稱指派,不在此寫 GUID
param baseName string
param location string
@description('探針模型部署 TPM(千 tokens/分)')
param probeModelCapacity int = 50
param probeModelName string = 'gpt-4.1-mini'
param probeModelVersion string = '2025-04-14'
param deploymentSku string = 'Standard'
param projectName string = 'claimcrew'
@description('判定/修補模型(Repair agent 與 Agent Service 上的判定臂);版本與 oai 帳戶主模型一致')
param decisionModelName string = 'gpt-4o'
param decisionModelVersion string = '2024-11-20'
param decisionModelCapacity int = 50
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}

#disable-next-line BCP081 // Bicep 0.30.23 的型別庫尚無 2025-06-01;API 版本依 Foundry 專案文件,桌機 what-if 為最終驗證
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'fdy-${baseName}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: 'fdy-${baseName}'
    publicNetworkAccess: 'Enabled'
  }
}

#disable-next-line BCP081
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: 'ClaimCrew SPEC-n Agent Service 探針專案'
  }
}

#disable-next-line BCP081
resource decisionModel 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: decisionModelName
  dependsOn: [project] // 專案與部署同掛帳戶,並行建立會 RequestConflict(2026-10-02 p2n 首次部署實證)
  sku: { name: deploymentSku, capacity: decisionModelCapacity }
  properties: {
    versionUpgradeOption: 'NoAutoUpgrade'
    model: { format: 'OpenAI', name: decisionModelName, version: decisionModelVersion }
  }
}

#disable-next-line BCP081
resource probeModel 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: probeModelName
  dependsOn: [decisionModel] // deployments 需序列建立
  sku: { name: deploymentSku, capacity: probeModelCapacity }
  properties: {
    versionUpgradeOption: 'NoAutoUpgrade' // 基準綁定模型版本,禁止 Azure 自動升版
    model: { format: 'OpenAI', name: probeModelName, version: probeModelVersion }
  }
}

output accountName string = foundry.name
output projectEndpoint string = 'https://fdy-${baseName}.services.ai.azure.com/api/projects/${projectName}'
