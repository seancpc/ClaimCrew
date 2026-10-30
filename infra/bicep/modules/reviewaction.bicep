// SPEC-am 覆核閉環(⑫ 方案 A):摘要信卡片「填寫覆核」連結 → ① 表單頁(GET,只顯示不寫入)→ ② 送出(POST)寫 Blob
// 安全設計:①GET 不寫入(防 Safe Links 預開連結代送)②Blob 檔名 = guid(),不以使用者輸入組路徑(防路徑注入),欄位原樣落盤、
// 合法性由 app/review/record.py 離線驗證 ③表單回填 run / case 一律 uriComponent()(防 HTML 注入),送出端 decodeUriComponent()
// ② 以系統指派身分直呼 Blob REST(無 API 連線),角色只授 runs 容器的 Storage Blob Data Contributor
// 觸發 URL 含 SAS 簽章:本模組不 output callback URL;Job 由 containerapp 模組以 listCallbackUrl 取得進 secret
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}
param storageAccountName string

var roleBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var inboxPath = 'runs/reviews-inbox'

resource runsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  name: '${storageAccountName}/default/runs'
}

resource submit 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'logic-${baseName}-review-submit'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        manual: { type: 'Request', kind: 'Http', inputs: { method: 'POST' } }
      }
      actions: {
        Put_review_blob: {
          type: 'Http'
          inputs: {
            method: 'PUT'
            uri: '@{concat(\'https://${storageAccountName}.blob.${environment().suffixes.storage}/${inboxPath}/\', guid(), \'.json\')}'
            headers: {
              'x-ms-blob-type': 'BlockBlob'
              'x-ms-version': '2021-08-06'
              'Content-Type': 'application/json'
            }
            body: {
              run_id: '@{decodeUriComponent(triggerFormDataValue(\'run\'))}'
              claim_id: '@{decodeUriComponent(triggerFormDataValue(\'case\'))}'
              decision: '@{triggerFormDataValue(\'decision\')}'
              amount: '@{triggerFormDataValue(\'amount\')}'
              note: '@{triggerFormDataValue(\'note\')}'
              submitted_at: '@{utcNow()}'
              user_agent: '@{triggerOutputs()?[\'headers\']?[\'User-Agent\']}'
            }
            authentication: { type: 'ManagedServiceIdentity', audience: 'https://storage.azure.com/' }
          }
        }
        Response_ok: {
          type: 'Response'
          kind: 'Http'
          runAfter: { Put_review_blob: ['Succeeded'] }
          inputs: {
            statusCode: 200
            headers: { 'Content-Type': 'text/html; charset=utf-8' }
            body: '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><p>已記錄,可關閉此頁。</p></body></html>'
          }
        }
        Response_failed: {
          type: 'Response'
          kind: 'Http'
          runAfter: { Put_review_blob: ['Failed', 'TimedOut'] }
          inputs: {
            statusCode: 502
            headers: { 'Content-Type': 'text/html; charset=utf-8' }
            body: '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><p>記錄失敗,請稍後重試。</p></body></html>'
          }
        }
      }
      outputs: {}
    }
  }
}

// ② 的身分只能寫 runs 容器(不授帳戶層角色)
resource submitBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runsContainer.id, submit.id, roleBlobDataContributor)
  scope: runsContainer
  properties: {
    principalId: submit.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleBlobDataContributor)
  }
}

var submitUrl = listCallbackUrl('${submit.id}/triggers/manual', '2019-05-01').value
var formHead = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="font-family:sans-serif"><h2>ClaimCrew 覆核</h2>'

resource form 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'logic-${baseName}-review-form'
  location: location
  tags: tags
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        manual: { type: 'Request', kind: 'Http', inputs: { method: 'GET' } }
      }
      actions: {
        Response_form: {
          type: 'Response'
          kind: 'Http'
          inputs: {
            statusCode: 200
            headers: { 'Content-Type': 'text/html; charset=utf-8' }
            body: '@{concat(\'${formHead}<p>run:\', uriComponent(coalesce(triggerOutputs()?[\'queries\']?[\'run\'], \'\')), \'  案號:\', uriComponent(coalesce(triggerOutputs()?[\'queries\']?[\'case\'], \'\')), \'</p><form method="post" action="${submitUrl}"><input type="hidden" name="run" value="\', uriComponent(coalesce(triggerOutputs()?[\'queries\']?[\'run\'], \'\')), \'"><input type="hidden" name="case" value="\', uriComponent(coalesce(triggerOutputs()?[\'queries\']?[\'case\'], \'\')), \'"><p><label><input type="radio" name="decision" value="approve" required> 核准</label> <label><input type="radio" name="decision" value="deny"> 拒付</label> <label><input type="radio" name="decision" value="manual_review"> 維持人工</label></p><p>核付金額(可空):<input type="text" name="amount" value=""></p><p>備註(可空,勿填姓名):<br><textarea name="note" rows="3" cols="40"></textarea></p><p><button type="submit">送出</button></p></form></body></html>\')}'
          }
        }
      }
      outputs: {}
    }
  }
}

output formWorkflowName string = form.name
output submitWorkflowName string = submit.name
