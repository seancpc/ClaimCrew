// ClaimCrew 一鍵部署入口(subscription scope:建資源群組後掛全部模組)
// 部署:az deployment sub create --location <region> --template-file main.bicep --parameters baseName=<name>
// 拆除:az group delete --name <rg-name>(拆建即成本控制,SPEC §8)
targetScope = 'subscription'

@description('資源命名前綴(小寫英數,3-15 字)')
@minLength(3)
@maxLength(15)
param baseName string = 'claimcrew'

@description('部署 region;預設 eastus 為 DI F0 / Search free / OpenAI 模型可用性的已知可用區域,換區前先查各服務區域配額')
param location string = 'eastus'

@description('Azure OpenAI 帳戶所在區。2026-09-30:eastus 的 gpt-5.6-luna 配額申請因區域容量被拒、表單不問區域而以帳戶所在區為準,故獨立此參數移至 eastus2(Standard gpt-4o 50K、11-20 Legacy 皆已驗證);其餘資源留 location')
param openAiLocation string = 'eastus2'

@description('Phase 2 服務(Content Safety / Logic Apps / App Insights / Container Apps / Event Grid);Phase 1 預設不建以壓成本')
param enablePhase2 bool = false

@description('ADR-007 分層路由實測臂:部署 gpt-4.1-mini(SPEC-k,2026-10-02)')
param enableMiniModel bool = true

@description('Foundry Agent Service(SPEC-n 探針 / SPEC-o Repair):Foundry 帳戶 + 專案 + gpt-4o + gpt-4.1-mini;Phase 1 拆建不受影響,預設不建')
param enableFoundry bool = false

@description('SPEC-r 人工覆核卡片:Logic Apps(Consumption)+ Outlook.com 連線;與 enablePhase2 解耦,預設不建')
param enableReview bool = false

@description('覆核卡片收件信箱(enableReview=true 時必填)')
param reviewEmailTo string = ''

// 2026-10-10:桌機 session 未設 $mail → reviewEmailTo='' 部署成功,Logic App 收件人被清空、無任何錯誤。
// 需要寄信的開關(覆核卡片 / Job 失敗告警)開啟而收件人為空 → 部署直接失敗;兩個模組一律引用本變數
var reviewEmail = (enableReview || ((enablePhase2 || enableMonitoring) && enableJobAlert)) && empty(trim(reviewEmailTo))
  ? fail('reviewEmailTo 不得為空:enableReview 或 enableJobAlert 開啟時必須提供收件信箱')
  : reviewEmailTo

@description('SPEC-am 覆核閉環:摘要信卡片附覆核表單連結 → 結果寫 runs/reviews-inbox(需 enableReview);預設不建')
param enableReviewAction bool = false

@description('SPEC-t Container Apps Job(ACR Basic + 使用者指派身分 + RBAC + Job);與 enablePhase2 解耦,預設不建')
param enableJob bool = false

@description('SPEC-v Content Safety(F0;groundedness 影子欄位 + 四類掃描);與 enablePhase2 解耦,預設不建')
param enableContentSafety bool = false

@description('Job 映像 tag = git SHA 前 7 碼;空字串 = 只建 ACR/身分/RBAC/環境、不建 Job(先部署空 → az acr build → 再部署 tag;禁 latest)')
param imageTag string = ''

@description('SPEC-ah 監控:Log Analytics + App Insights + Job 失敗告警(寄 reviewEmailTo);與 enablePhase2 解耦,預設不建')
param enableMonitoring bool = false

@description('SPEC-ah:Job 失敗告警規則(需 enableMonitoring;新 workspace 須等 Job 首次執行產生日誌後才開,runbook 4.23)')
param enableJobAlert bool = false

@description('SPEC-ah 事件觸發:Event Grid 訂閱(claims-inbox/requests/*.json)→ Storage Queue → 事件 Job;需 enableJob,預設不建')
param enableEventTrigger bool = false

@description('Container Apps 環境 + Job 所在區。2026-10-08 ⑧ 重建:eastus 回 AKSCapacityHeavyUsage(兩次、間隔 70 分鐘)→ 移至 eastus2(與 OpenAI 同區);ACR / 身分 / Storage / 日誌留 location')
param containerAppsLocation string = 'eastus2'

@description('Foundry 帳戶所在區。2026-10-03:eastus2 Standard gpt-4o 配額已被 oai 帳戶用滿,eastus 50K 未用 → 獨立參數')
param foundryLocation string = 'eastus'

@description('成本歸屬標籤:所有資源與資源群組一致,供成本分析依 project/phase 切分')
param tags object = {
  project: 'ClaimCrew'
  phase: enablePhase2 ? 'phase2' : 'phase1'
  owner: 'personal'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${baseName}'
  location: location
  tags: tags
}

module storage 'modules/storage.bicep' = {
  scope: rg
  name: 'storage'
  params: { baseName: baseName, location: location, tags: tags }
}

module docintel 'modules/docintel.bicep' = {
  scope: rg
  name: 'docintel'
  params: { baseName: baseName, location: location, tags: tags }
}

module search 'modules/search.bicep' = {
  scope: rg
  name: 'search'
  params: { baseName: baseName, location: location, tags: tags }
}

module openai 'modules/openai.bicep' = {
  scope: rg
  name: 'openai'
  params: { baseName: baseName, location: openAiLocation, tags: tags, enableMiniModel: enableMiniModel }
}

// --- Foundry Agent Service(enableFoundry=true 才部署;SPEC-o 起在 foundryLocation=eastus)---
module foundry 'modules/foundry.bicep' = if (enableFoundry) {
  scope: rg
  name: 'foundry'
  params: { baseName: baseName, location: foundryLocation, tags: tags }
}

// --- Phase 2 模組(enablePhase2=true 才部署)---
module contentsafety 'modules/contentsafety.bicep' = if (enablePhase2 || enableContentSafety) {
  scope: rg
  name: 'contentsafety'
  params: { baseName: baseName, location: location, tags: tags }
}

module monitoring 'modules/monitoring.bicep' = if (enablePhase2 || enableMonitoring) {
  scope: rg
  name: 'monitoring'
  params: { baseName: baseName, location: location, tags: tags, alertEmail: reviewEmail, enableJobAlert: enableJobAlert }
}

module logicapp 'modules/logicapp.bicep' = if (enableReview) {
  scope: rg
  name: 'logicapp'
  params: { baseName: baseName, location: location, tags: tags, reviewEmailTo: reviewEmail }
}

module reviewaction 'modules/reviewaction.bicep' = if (enableReview && enableReviewAction) {
  scope: rg
  name: 'reviewaction'
  params: { baseName: baseName, location: location, tags: tags, storageAccountName: storage.outputs.accountName }
}

module containerapp 'modules/containerapp.bicep' = if (enableJob) {
  scope: rg
  name: 'containerapp'
  params: {
    baseName: baseName
    location: location
    appsLocation: containerAppsLocation
    tags: tags
    imageTag: imageTag
    storageAccountName: storage.outputs.accountName
    diAccountName: docintel.outputs.accountName
    openAiAccountName: openai.outputs.accountName
    reviewWorkflowName: enableReview ? logicapp!.outputs.workflowName : ''
    reviewActionWorkflowName: (enableReview && enableReviewAction) ? reviewaction!.outputs.formWorkflowName : ''
    csAccountName: (enablePhase2 || enableContentSafety) ? contentsafety!.outputs.accountName : ''
    logWorkspaceName: (enablePhase2 || enableMonitoring) ? monitoring!.outputs.workspaceName : ''
    enableEventTrigger: enableEventTrigger
    requestQueueName: storage.outputs.requestQueueName
  }
}

module eventgrid 'modules/eventgrid.bicep' = if (enablePhase2 || enableEventTrigger) {
  scope: rg
  name: 'eventgrid'
  params: {
    baseName: baseName
    location: location
    tags: tags
    storageAccountId: storage.outputs.accountId
    queueName: storage.outputs.requestQueueName
  }
}

output resourceGroupName string = rg.name
output storageAccountName string = storage.outputs.accountName
output docIntelEndpoint string = docintel.outputs.endpoint
output searchEndpoint string = search.outputs.endpoint
output openAiEndpoint string = openai.outputs.endpoint
output foundryProjectEndpoint string = foundry.?outputs.projectEndpoint ?? ''
// Phase 2 條件式 output(未啟用時為空字串),部署後不必回 Portal 撈值
output contentSafetyEndpoint string = contentsafety.?outputs.endpoint ?? ''
output appInsightsConnectionString string = monitoring.?outputs.connectionString ?? ''
output reviewWorkflowId string = logicapp.?outputs.workflowId ?? ''
output jobName string = containerapp.?outputs.jobName ?? ''
output eventJobName string = containerapp.?outputs.eventJobName ?? ''
output acrLoginServer string = containerapp.?outputs.acrLoginServer ?? ''
output blobEventTopicId string = eventgrid.?outputs.topicId ?? ''
