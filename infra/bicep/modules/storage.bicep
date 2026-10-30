// 文件收件匣:Standard LRS,趨零成本(SPEC §8)
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  // 全域唯一 + 小寫 + ≤24:st + baseName(去連字號)+ 資源群組雜湊,截 24
  name: take(toLower('st${replace(baseName, '-', '')}${uniqueString(resourceGroup().id)}'), 24)
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    // DI / Content Understanding Studio 讀取 di-training 容器所需(Studio 首次連線會自行加,
    // 不宣告的話重部署即抹掉;2026-09-30 what-if 揭露)
    cors: {
      corsRules: [
        {
          allowedOrigins: [
            'https://contentunderstanding.ai.azure.com'
            'https://documentintelligence.ai.azure.com'
          ]
          allowedMethods: ['DELETE', 'GET', 'HEAD', 'MERGE', 'OPTIONS', 'PATCH', 'POST', 'PUT']
          allowedHeaders: ['*']
          exposedHeaders: ['*']
          maxAgeInSeconds: 120
        }
      ]
    }
  }
}

resource inbox 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'claims-inbox'
}

// SPEC-t:雲端 Job 的輸出(runs/<run_id>/)與 DI 磁碟快取同步(鍵 = PDF sha256);免費、拆建隨帳戶走
resource runs 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'runs'
}
resource diCache 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'di-cache'
}

// SPEC-ah:事件觸發佇列(Event Grid 投遞 requests/*.json 的 BlobCreated → Job KEDA azure-queue 規則);不用不計費
resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: account
  name: 'default'
}
resource requestQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'claim-requests' // 與 tools/cloud_run.py QUEUE_NAME 一致
}

output accountName string = account.name
output accountId string = account.id
output requestQueueName string = requestQueue.name
