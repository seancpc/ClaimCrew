// Container Apps Job(SPEC-t,2026-10-04):無人值守跑一輪管線——手動觸發、跑完即停(scale-to-zero)、
// 使用者指派身分無金鑰(DI / AOAI / Blob / ACR 皆 RBAC)、映像 tag 釘 git SHA(pre-mortem:禁 latest)。
// 取代原 Phase 2 佔位的「API + 審核 UI」常駐 app(目前使用型態是批次 72 案 + 一封摘要,無單案即時需求)。
param baseName string
param location string
@description('Container Apps 環境與 Job 所在區(2026-10-08:eastus AKSCapacityHeavyUsage 無法建新環境 → 獨立參數);身分 / ACR / 日誌留 location')
param appsLocation string = location
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}
@description('映像 tag(git SHA 前 7 碼)。**空字串 = 只建 ACR/身分/RBAC/環境、不建 Job**(2026-10-04 實證:Job 建立時 ARM 會驗證 manifest,映像未推會 MANIFEST_UNKNOWN);重建順序:部署(空)→ az acr build → 部署(tag)')
param imageTag string = ''
param storageAccountName string
param diAccountName string
param openAiAccountName string
@description('覆核摘要 Logic App 工作流名;空字串 = Job 不帶 --review')
param reviewWorkflowName string = ''
@description('SPEC-am 覆核表單 workflow 名稱;空 = 卡片不附覆核連結')
param reviewActionWorkflowName string = ''
@description('SPEC-v:Content Safety 帳戶名;空字串 = 不加角色、Job 不帶 --safety')
param csAccountName string = ''
@description('Job 單次執行上限秒數(72 案 DI 快取命中約 15~20 分鐘)')
param replicaTimeoutSeconds int = 3600
@description('SPEC-ah:共用 Log Analytics 名(monitoring 模組);空字串 = 沿用 Job 自建 log-<base>-job')
param logWorkspaceName string = ''
@description('SPEC-ah:建事件觸發 Job(KEDA azure-queue 輪詢 requestQueueName,一則訊息一次執行)')
param enableEventTrigger bool = false
param requestQueueName string = 'claim-requests'

// 角色定義 ID(內建,全訂閱相同)
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleBlobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var roleCognitiveUser = 'a97b65f3-24c7-4388-baec-2e87135dc908' // DI 資料面(2026-10-04 桌機 di_smoke --entra 8/8 驗證)
var roleOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // AOAI 資料面(aoai_smoke --entra 驗證)
var roleQueueProcessor = '8a0f0c08-91a1-4084-bc3d-661d67233fed' // Storage Queue Data Message Processor:取/刪訊息
var roleQueueReader = '19e7f393-937e-4f77-808e-94535e297925' // Storage Queue Data Reader:KEDA scaler 讀佇列長度(pre-mortem)
var acrName = take(toLower('acr${replace(baseName, '-', '')}${uniqueString(resourceGroup().id)}'), 50)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }
resource di 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = { name: diAccountName }
resource aoai 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = { name: openAiAccountName }
var safetyEnabled = !empty(csAccountName)
resource cs 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = if (safetyEnabled) { name: csAccountName }

// 使用者指派身分:先於 Job 存在,RBAC 才能在 Job 第一次拉映像前生效(系統指派身分做不到)
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${baseName}-job'
  location: location
  tags: tags
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Basic' } // 常駐約 NT$5/天;拆建原則下可接受,拆除時一併刪
  properties: { adminUserEnabled: false } // 只走身分 AcrPull,不開管理員帳密
}

var sharedLaw = !empty(logWorkspaceName)
resource lawOwn 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (!sharedLaw) {
  name: 'log-${baseName}-job'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // 成本護欄
  }
}

// 單一參照(共用或自建);直接對條件式資源 listKeys() 會讓 Bicep 0.36.1 編譯器崩潰
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: sharedLaw ? logWorkspaceName : 'log-${baseName}-job'
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${baseName}'
  location: appsLocation
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey // 僅供環境設定,不作為任何 output 外流
      }
    }
  }
  dependsOn: [lawOwn]
}

// RBAC:名稱以 (身分, 角色, 範圍) 決定,重部署冪等
resource raAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, roleAcrPull)
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}
resource raBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, roleBlobContributor)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleBlobContributor)
  }
}
resource raDi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(di.id, identity.id, roleCognitiveUser)
  scope: di
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveUser)
  }
}
resource raAoai 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoai.id, identity.id, roleOpenAiUser)
  scope: aoai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleOpenAiUser)
  }
}

resource raCs 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (safetyEnabled) {
  name: guid(cs.id, identity.id, roleCognitiveUser)
  scope: cs
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveUser)
  }
}

// SPEC-ah:事件 Job 讀/刪佇列訊息(只授佇列資料面兩個角色,範圍為儲存體帳戶)
resource raQueueProc 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableEventTrigger) {
  name: guid(storage.id, identity.id, roleQueueProcessor)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleQueueProcessor)
  }
}
resource raQueueRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableEventTrigger) {
  name: guid(storage.id, identity.id, roleQueueReader)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleQueueReader)
  }
}

// 覆核摘要觸發 URL(含 SAS 簽章)直接由 ARM 取得進 Job secret,不經人手、不落檔
var reviewEnabled = !empty(reviewWorkflowName)
var reviewSecrets = reviewEnabled ? [
  {
    name: 'review-webhook-url'
    value: listCallbackUrl(resourceId('Microsoft.Logic/workflows/triggers', reviewWorkflowName, 'manual'), '2019-05-01').value
  }
] : []
var reviewEnv = reviewEnabled ? [{ name: 'REVIEW_WEBHOOK_URL', secretRef: 'review-webhook-url' }] : []
// SPEC-am:覆核表單觸發 URL(含 sig)同樣由 ARM 取得進 secret;卡片連結 = 此 URL + run / case
var reviewActionEnabled = reviewEnabled && !empty(reviewActionWorkflowName)
var reviewActionSecrets = reviewActionEnabled ? [
  {
    name: 'review-action-url'
    value: listCallbackUrl(resourceId('Microsoft.Logic/workflows/triggers', reviewActionWorkflowName, 'manual'), '2019-05-01').value
  }
] : []
var reviewActionEnv = reviewActionEnabled ? [{ name: 'REVIEW_ACTION_URL', secretRef: 'review-action-url' }] : []

var jobArgs = concat(reviewEnabled ? ['--review'] : [], safetyEnabled ? ['--safety'] : [], ['--pace', '5'])
var jobEnv = concat([
  { name: 'CLAIMCREW_AUTH', value: 'entra' }
  { name: 'CLAIMCREW_STORAGE_ACCOUNT', value: storageAccountName }
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential 選用此使用者指派身分
  { name: 'GIT_SHA', value: imageTag }
], reviewEnv, reviewActionEnv, safetyEnabled ? [{ name: 'CS_ENDPOINT', value: cs!.properties.endpoint }] : [])
var jobImage = '${acr.properties.loginServer}/claimcrew:${imageTag}'

resource job 'Microsoft.App/jobs@2025-01-01' = if (!empty(imageTag)) {
  name: 'caj-${baseName}-pipeline'
  location: appsLocation
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 0 // 失敗不自動重跑(LLM 計費),由人看 run_meta 決定
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: acr.properties.loginServer, identity: identity.id }]
      secrets: concat(reviewSecrets, reviewActionSecrets)
    }
    template: {
      containers: [
        {
          name: 'pipeline'
          image: jobImage
          args: jobArgs
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: jobEnv
        }
      ]
    }
  }
  dependsOn: [raAcr, raBlob, raDi, raAoai, raCs]
}

// SPEC-ah 事件觸發 Job:同映像、同身分;一則佇列訊息 = 一次執行(cloud_run --from-queue 先刪後跑)。
// maxExecutions 1 + retry 0:佇列暴增也只序列處理,失敗不重跑(LLM 計費護欄)
resource eventJob 'Microsoft.App/jobs@2025-01-01' = if (enableEventTrigger && !empty(imageTag)) {
  name: 'caj-${baseName}-event'
  location: appsLocation
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Event'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 0
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 60
          rules: [
            {
              name: 'claim-requests'
              type: 'azure-queue'
              metadata: { accountName: storageAccountName, queueName: requestQueueName, queueLength: '1' }
              identity: identity.id // 無金鑰:KEDA 以使用者指派身分讀佇列長度
            }
          ]
        }
      }
      registries: [{ server: acr.properties.loginServer, identity: identity.id }]
      secrets: concat(reviewSecrets, reviewActionSecrets)
    }
    template: {
      containers: [
        {
          name: 'pipeline'
          image: jobImage
          args: concat(['--from-queue'], jobArgs)
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(jobEnv, [{ name: 'CLAIMCREW_QUEUE_NAME', value: requestQueueName }])
        }
      ]
    }
  }
  dependsOn: [raAcr, raBlob, raDi, raAoai, raCs, raQueueProc, raQueueRead]
}

output jobName string = empty(imageTag) ? '' : job.name
output eventJobName string = (enableEventTrigger && !empty(imageTag)) ? eventJob.name : ''
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output identityPrincipalId string = identity.properties.principalId
