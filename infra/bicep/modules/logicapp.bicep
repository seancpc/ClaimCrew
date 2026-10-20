// Logic Apps(Consumption):人工覆核卡片工作流(SPEC-r,2026-10-03;ADR-008 顧問輸出的讀者)
// 流程:HTTP Request 觸發(程式送 ReviewCard payload)→ Outlook.com 寄信 → Response 200(同步:寄信失敗則呼叫端收到 502,
// 不會像非同步 202 那樣誤記 sent;pre-mortem 對策)。渲染在程式端,此處只收→寄→回應。
// 注意:Outlook.com API 連線的 OAuth 同意無法由 Bicep 完成,部署後須在 Portal 對 `outlook-<baseName>` 按一次「授權」(runbook 4.17)
// 觸發 URL 含 SAS 簽章,屬秘密:本模組**不** output callback URL,由 `az rest` listCallbackUrl 取得後只放環境變數
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}
@description('覆核卡片收件信箱')
param reviewEmailTo string

var connectionName = 'outlook-${baseName}'

resource outlook 'Microsoft.Web/connections@2016-06-01' = {
  name: connectionName
  location: location
  tags: tags
  properties: {
    displayName: connectionName
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'outlook')
    }
  }
}

resource wf 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'logic-${baseName}-review'
  location: location
  tags: tags
  properties: {
    state: 'Enabled'
    parameters: {
      '$connections': {
        value: {
          outlook: {
            connectionId: outlook.id
            connectionName: connectionName
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'outlook')
          }
        }
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': { type: 'Object', defaultValue: {} }
      }
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            // 與 app/review/card.py::card_payload 一致(欄位名為契約;固定解法 4)
            // SPEC-s:摘要信(subject/html/claim_ids/case_count)與逐案卡片(claim_id/…)共用;只強制 subject + html
            schema: {
              type: 'object'
              required: ['subject', 'html']
              properties: {
                subject: { type: 'string' }
                html: { type: 'string' }
                claim_id: { type: 'string' }
                final_decision: { type: 'string' }
                original_decision: { type: 'string' }
                approved_amount: { type: 'string' }
                claim_ids: { type: 'array', items: { type: 'string' } }
                case_count: { type: 'integer' }
              }
            }
          }
        }
      }
      actions: {
        Send_review_email: {
          type: 'ApiConnection'
          runAfter: {}
          inputs: {
            host: { connection: { name: '@parameters(\'$connections\')[\'outlook\'][\'connectionId\']' } }
            method: 'post'
            path: '/v2/Mail'
            body: {
              To: reviewEmailTo
              Subject: '@triggerBody()?[\'subject\']'
              Body: '@triggerBody()?[\'html\']'
              IsHtml: true
              Importance: 'Normal'
            }
          }
        }
        Respond_ok: {
          type: 'Response'
          kind: 'Http'
          runAfter: { Send_review_email: ['Succeeded'] }
          inputs: {
            statusCode: 200
            body: { claim_id: '@triggerBody()?[\'claim_id\']', sent: true }
          }
        }
        Respond_failed: {
          type: 'Response'
          kind: 'Http'
          runAfter: { Send_review_email: ['Failed', 'TimedOut'] }
          inputs: {
            statusCode: 502
            // 2026-10-03 實測:ApiConnection 失敗時 ['error'] 為 null,錯誤碼在 code、連接器訊息在 outputs.body
            body: {
              claim_id: '@triggerBody()?[\'claim_id\']'
              sent: false
              code: '@actions(\'Send_review_email\')?[\'code\']'
              error: '@coalesce(actions(\'Send_review_email\')?[\'error\'], actions(\'Send_review_email\')?[\'outputs\']?[\'body\'])'
            }
          }
        }
      }
      outputs: {}
    }
  }
}

output workflowId string = wf.id
output workflowName string = wf.name
output connectionName string = outlook.name
