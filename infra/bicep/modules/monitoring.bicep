// Application Insights + Log Analytics:延遲/token 成本/每案成本儀表(SPEC §8,免費額度內)
// SPEC-ah:Job 環境改用此 workspace(不再另建 log-<base>-job);Job 失敗 → 寄信告警
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}
@description('SPEC-ah:Job 失敗告警收件信箱;空字串 = 不建 Action Group / 告警')
param alertEmail string = ''
@description('SPEC-ah:建 Job 失敗告警規則。新 workspace 首筆 Container Apps 日誌寫入前不得開(見規則上方註解)')
param enableJobAlert bool = false

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${baseName}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // 成本護欄:日上限 1GB
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${baseName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
  }
}

// SPEC-ah:Job 失敗告警。錨點有二——cloud_run 自身輸出的「: failed exit=」(含壞請求 exit 2、管線非零),
// 與平台層 replica 失敗(容器崩潰、逾時,cloud_run 來不及印)。表名/欄位需於重建後真實觸發驗證(假設 1)。
var alertsEnabled = !empty(alertEmail)

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (alertsEnabled) {
  name: 'ag-${baseName}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take('cc${replace(baseName, '-', '')}', 12)
    enabled: true
    emailReceivers: [{ name: 'owner', emailAddress: alertEmail, useCommonAlertSchema: true }]
  }
}

// 新建 workspace 尚無 ContainerAppConsoleLogs_CL / ContainerAppSystemLogs_CL(首筆日誌寫入才建),建立規則時 ARM 回
// 「Operator source expression should be table or column」;skipQueryValidation 實測無效(2026-10-08 ⑧ 重建兩次)
// → 分兩段:先部署、Job 跑過一次產生日誌後,再以 enableJobAlert=true 重部署建立本規則
resource jobFailed 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = if (alertsEnabled && enableJobAlert) {
  name: 'alert-${baseName}-job-failed'
  location: location
  tags: tags
  properties: {
    displayName: 'ClaimCrew Job 執行失敗'
    severity: 2
    enabled: true
    scopes: [law.id]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: '''
union isfuzzy=true
  (ContainerAppConsoleLogs_CL | where Log_s has "cloud_run" and Log_s has ": failed exit="),
  (ContainerAppSystemLogs_CL | where Reason_s in ("BackoffLimitExceeded", "ReplicaFailed", "DeadlineExceeded"))
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    autoMitigate: false
    actions: { actionGroups: [actionGroup.id] }
  }
}

output connectionString string = appi.properties.ConnectionString
output workspaceId string = law.id
output workspaceName string = law.name
