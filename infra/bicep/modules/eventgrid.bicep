// Event Grid 系統主題:Blob 上傳事件(SPEC §5.1 觸發源)
// SPEC-ah:訂閱只收 claims-inbox/requests/*.json 的 BlobCreated → Storage Queue(Job 以 KEDA 輪詢佇列觸發)
param baseName string
param location string
@description('成本歸屬標籤(由 main 統一傳入)')
param tags object = {}
param storageAccountId string
@description('投遞目標佇列名(storage 模組 output)')
param queueName string

resource topic 'Microsoft.EventGrid/systemTopics@2022-06-15' = {
  name: 'evgt-${baseName}-blob'
  location: location
  tags: tags
  properties: {
    source: storageAccountId
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

resource requests 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15' = {
  parent: topic
  name: 'claim-requests-to-queue'
  properties: {
    destination: {
      endpointType: 'StorageQueue'
      properties: {
        resourceId: storageAccountId
        queueName: queueName
        queueMessageTimeToLiveInSeconds: 86400 // 一天內沒被取走即丟棄,不累積成日後一次爆量
      }
    }
    filter: {
      includedEventTypes: ['Microsoft.Storage.BlobCreated']
      subjectBeginsWith: '/blobServices/default/containers/claims-inbox/blobs/requests/'
      subjectEndsWith: '.json'
    }
    eventDeliverySchema: 'EventGridSchema'
    retryPolicy: { maxDeliveryAttempts: 5, eventTimeToLiveInMinutes: 60 }
  }
}

output topicId string = topic.id
output subscriptionName string = requests.name
