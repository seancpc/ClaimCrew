# ClaimCrew 操作 Runbook(桌機 × 雲端 session)

> 定位:**「上次成功的步驟 + 版本釘選」**,每次重啟、每次踩坑當下回填,不追求即時維護(SPEC §10)。
> 到 Phase 3 擴充為「拆除後重建」手冊。指令皆為 Windows PowerShell;雲端 session 另註。
> 最後更新:2026-10-04

---

## 0. 兩邊分工(不變原則)

| 誰 | 做什麼 | 不做什麼 |
|---|---|---|
| **雲端 session(Claude)** | 寫程式 / 改 Bicep / 寫測試 / 生成合成資料與 PDF / commit、push 到 `development` | 不碰 Azure 憑證、不部署 |
| **桌機(我)** | `git pull` → 執行需要 Azure 憑證的事(`az deployment`、DI 訓練上傳、真實 eval)→ 把輸出/截圖貼回對話 | **不跑 `render_pdf` / `cases --force`**(字型不同會讓 220 個 PDF 位元組全變) |

---

## 0.5 桌機:環境狀態速查(什麼情況要重做什麼)

> 原則:**金鑰與觸發 URL 只活在當前 PowerShell 視窗**,任何「關視窗 / 重開機 / 開新終端」都要重設;其餘東西都在磁碟或 Azure 上,不用重做。

| 情況 | 要重做 | 不用重做 |
|---|---|---|
| 開新 PowerShell 視窗 / 重開機 | ① 進資料夾 + 啟用 venv ② `git pull` ③ **全部環境變數**(見下方 A) | venv 本身、`az login`(token 通常存活數天)、DI 磁碟快取 `check\di_cache`、已部署的 Azure 資源 |
| `az` 指令回 `Please run 'az login'` / `AADSTS` 錯 | `az login`;再確認 `az account show --query name -o tsv` 為 `claimcrew` | 其他都不用 |
| 雲端 session 說「已推送」 | `git pull origin development`;若 `requirements*.txt` 變動 → `pip install -r requirements.txt -r requirements-dev.txt` | 環境變數 |
| 要跑 `run_pipeline` / `di_smoke` / `aoai_smoke` | A 的 `DI_KEY`、`AOAI_KEY`(`core+search` 另加 `SEARCH_KEY`) | |
| 要送覆核卡片 / 摘要(`--review`、`send_review`) | A 的 `REVIEW_WEBHOOK_URL`(4.17 步驟 4);信箱若被封鎖先解鎖(W15) | Logic App 與連線授權(已在 Azure 上,除非重拆) |
| 要跑探針 / Repair(4.14、4.15、`rerepair`) | `az login` 有效 + A 的 `FOUNDRY_PROJECT_ENDPOINT`;第二意見另需 `AOAI_KEY` | |
| 剛 `az group delete` 重建過 | 4.5 部署 → 4.10 DI 重訓 → 4.12 索引重建 → 4.17 步驟 3 Portal 重新授權 Outlook 連線 → 全部環境變數 | 程式碼、合成資料 |

**A. 環境變數一鍵區**(每個新視窗照貼;純讀取金鑰、不落檔;用不到的可省):
```powershell
cd C:\ClaimCrew
.\.venv\Scripts\Activate.ps1
git pull origin development
az account show --query name -o tsv                                                                     # 應印 claimcrew;否則 az login
$env:DI_KEY     = (az cognitiveservices account keys list -n di-claimcrew  -g rg-claimcrew --query key1 -o tsv)
$env:AOAI_KEY   = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
$env:SEARCH_KEY = (az search admin-key show --service-name srch-claimcrew -g rg-claimcrew --query primaryKey -o tsv)   # 只有 core+search / search_index 需要
$env:FOUNDRY_PROJECT_ENDPOINT = "https://fdy-claimcrew.services.ai.azure.com/api/projects/claimcrew"                 # 探針 / Repair
$rid = az resource show -g rg-claimcrew -n logic-claimcrew-review --resource-type Microsoft.Logic/workflows --query id -o tsv
$env:REVIEW_WEBHOOK_URL = az rest --method post --uri "$rid/triggers/manual/listCallbackUrl?api-version=2019-05-01" --query value -o tsv   # 覆核卡片
$env:DI_KEY.Length; $env:AOAI_KEY.Length; $env:REVIEW_WEBHOOK_URL.Length                                 # 皆 > 0 即可,不要印值
# 部署用變數(W25):真實信箱只放 check\env.ps1(check/ 已 gitignore,不進版控)
if (Test-Path check\env.ps1) { . .\check\env.ps1 } else { Write-Warning "缺 check\env.ps1,見下方「首次建立」" }
$sha = (git rev-parse --short HEAD)
$p = "baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=$mail enableJob=true enableMonitoring=true enableEventTrigger=true enableJobAlert=true"
"mail=[$mail] sha=$sha"                                                                                  # 方括號內必須有信箱;空的 → 不得部署
```
- **首次建立 `check\env.ps1`**(每台桌機一次;單引號整行照貼,把占位文字換成實際信箱與 Tenant ID):
  ```powershell
  Set-Content -Encoding utf8 check\env.ps1 '$mail = "你的覆核信箱@outlook.com"'
  Add-Content -Encoding utf8 check\env.ps1 '$tenant = "你的 Tenant ID"'                                # az account show --query tenantId -o tsv 可查
  git check-ignore -v check\env.ps1                                                                     # 必須印出 .gitignore 規則;沒印 → 停,不得建立
  ```
- 部署一律在指令尾端帶 `--query "properties.parameters.reviewEmailTo.value" -o tsv`,印出值為信箱才算完成(W25);Bicep 自 2026-10-10 起收件人為空會 `fail()`,但仍要看輸出

---

## 1. 桌機:每次開工

```powershell
cd C:\ClaimCrew
.\.venv\Scripts\Activate.ps1          # 提示字元出現 (.venv)
git pull origin development           # 拉雲端 session 推的最新內容
python --version                      # 必須 3.11.x
```

**什麼時候要跑檢查**:pull 之後若 `requirements*.txt`、`app/`、`tests/` 有變動,跑一次四連(約 10 秒):
```powershell
python -m pytest -q                   # 預期 N passed(目前 583)
python -m mypy                        # 預期 Success: no issues found
python -m ruff check .                # 預期 All checks passed!
python -m eval.run_eval --selfcheck   # 預期 SELFCHECK: PASS
```
`requirements*.txt` 有變動時先 `pip install -r requirements.txt -r requirements-dev.txt` 再跑。

**Azure 操作前**(token 大約 1 天過期,`az account show` 報錯就重登):
```powershell
az account show --query "{sub:id, name:name}" -o table
az login                              # 上面報錯才需要
```

## 2. 桌機:首次建置(新機器 / 重灌才需要)

```powershell
cd <放專案的地方,路徑勿含中文或空格>
git clone https://github.com/seancpc/ClaimCrew.git
cd claimcrew
git config user.name "Sean Chuang"  # ★ 沒設會以本機預設身分 commit(2026-09-30 踩到,已改寫)
git config user.email "38429040+seancpc@users.noreply.github.com"
py -3.11 -m venv .venv                # 一定用 py -3.11,避免抓到舊版 Python
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
winget install Microsoft.AzureCLI     # 裝完開新視窗
az bicep install
```
裝完跑第 1 節的四連確認全綠。

## 3. 雲端 session:每次開工(Claude 自己做,列出供核對)

```bash
git fetch origin development && git checkout development   # 新容器預設不在 development
git config user.name "Sean Chuang"
git config user.email "38429040+seancpc@users.noreply.github.com"
pip install -r requirements.txt -r requirements-dev.txt     # 容器是拋棄式的
pip install --ignore-installed cffi cryptography            # 環境坑 A2
bash tools/gauntlet.sh                                      # 六層基準
```

## 4. Azure:訂閱與首次部署(Phase 1 進行中,逐步回填)

- [x] 4.0 工具:Azure CLI 2.90.0、Bicep 0.47.16(CI 釘 0.30.23;`az bicep build --file infra\bicep\main.bicep --stdout | Out-Null` 兩版皆 0 errors 0 warnings)
- [x] 4.1 Portal 開通 Pay-As-You-Go 訂閱(2026-09-30;路徑:Subscriptions → + Add → 「Upgrade Now」把試用帳戶升級,Support plan 選 Basic-Included;訂閱命名 `claimcrew`)
- [x] 4.2 成本護欄:訂閱層級預算 `claimcrew-monthly` 3,000 TWD/月(2026-09-30 設 1,500 → 10-04 調 2,000 → 10-07 調 3,000),Actual 50/80/100% 寄 Email
- [x] 4.3 記錄:Subscription ID `<SUBSCRIPTION_ID>`、Tenant ID `<TENANT_ID>`(實際值放 `check\env.ps1` 的 `$tenant`)、區域 `eastus`(2026-09-30 以 `az cognitiveservices model list -l eastus` 確認 gpt-4o 2024-08-06 與 gpt-4o-mini 2024-07-18 皆可部署)
- [x] 4.4 資源提供者註冊(2026-09-30 完成;一次性;`Registered` 才算完成,約 1~3 分鐘):
  ```powershell
  $providers = "Microsoft.CognitiveServices","Microsoft.Search","Microsoft.DocumentDB","Microsoft.Storage","Microsoft.App","Microsoft.EventGrid","Microsoft.Logic","Microsoft.OperationalInsights","Microsoft.Insights"
  foreach ($p in $providers) { az provider register --namespace $p --wait; az provider show -n $p --query "{ns:namespace, state:registrationState}" -o tsv }
  ```
- [x] 4.6 OpenAI 配額確認(2026-09-30):Standard gpt-4o **50K** / gpt-4o-mini 200K;GlobalStandard 全為 0;gpt-5.6-luna 0(申請中)。Bicep `deploymentSku` 預設 `Standard`、`modelCapacity` 10
  ```powershell
  az cognitiveservices usage list -l eastus --query "[?contains(name.value,'gpt-4o') || contains(name.value,'gpt-5.6')].{name:name.value, limit:limit}" -o table
  az cognitiveservices model list -l eastus --query "[?starts_with(model.name,'gpt-')].{name:model.name, version:model.version, status:model.lifecycleStatus, retire:model.deprecation.inference}" -o table
  ```
  換模型前兩條都要跑:status 為 `Deprecating` 的版本 preflight 會拒絕(W7),配額 0 的 SKU 部署會失敗
- [x] 4.5 首次部署(2026-09-30 `claimcrew-p1`,4 分 23 秒,11 個資源 Succeeded;模型 gpt-4o 2024-11-20 / Standard 單模型)。指令(重建時照跑,宣告式、可重複):
  ```powershell
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew
  az deployment sub create --name claimcrew-p1 --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew
  az deployment sub show --name claimcrew-p1 --query "properties.{state:provisioningState, outputs:outputs}" -o json
  ```
  outputs(公開主機名,非機密):
  | 資源 | 值 |
  |---|---|
  | resource group | `rg-claimcrew` |
  | storage | `stclaimcrew<自動產生>`(Bicep 依訂閱命名,指令中以 `$st` 取得;容器 `claims-inbox`) |
  | Document Intelligence | `https://di-claimcrew.cognitiveservices.azure.com/`(F0) |
  | AI Search | `https://srch-claimcrew.search.windows.net`(free) |
  | Azure OpenAI | `https://oai-claimcrew.openai.azure.com/`(部署名 `gpt-4o`;**帳戶在 eastus2**,2026-09-30 為 luna 配額搬遷,其餘資源 eastus) |
  | ~~Cosmos~~ | 已自 Bicep 移除(2026-10-07,ADR-010);現存帳戶於 ⑧ 拆除時消失,重建後不再有此端點 |
  失敗時貼完整錯誤給雲端 session;**不要**手動在 Portal 補資源(會脫離 Bicep 真相源)
- [ ] 4.7 用完即拆(壓縮計費窗口):`az group delete -n rg-claimcrew --yes --no-wait`;重建就是 4.5 再跑一次(Cognitive Services 有 48 小時 soft delete,同名重建若撞名先 `az cognitiveservices account purge`)
- [x] 4.8 gpt-5.6-luna 配額申請(**2026-09-30 結案:兩次被拒,停止追**)(入口:Foundry `oai-claimcrew` → 共用的資源 → 配額 → 標準+批次 → 要求配額表單;Portal 的 Quotas 服務**沒有** Azure OpenAI provider,別去找)
  - 第一次 Application Id 59695(2026-09-30):**被拒**,理由 East US 無法擴容;同區 30 天內不得重送,換區可立即重送
  - **表單不問區域**(aka.ms/oai/stuquotarequest 亦無 Region 欄),審核以訂閱內 Azure OpenAI 帳戶所在區為準 → 把 OpenAI 帳戶搬到 eastus2(Bicep `openAiLocation`,其餘資源留 eastus),再從新帳戶的配額頁重送
  - 搬遷順序(帳戶同名、customSubDomain 全域唯一,必須先刪+purge 才能在新區重建;endpoint 網址不變):
    ```powershell
    az cognitiveservices account delete -n oai-claimcrew -g rg-claimcrew
    az cognitiveservices account purge -n oai-claimcrew -g rg-claimcrew -l eastus
    az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew
    az deployment sub create --name claimcrew-p1 --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew
    ```
  - 2026-09-30 搬遷完成(刪+purge+重部署 1 分鐘,endpoint 不變);第二次申請(帳戶已在 eastus2)**數分鐘內再被拒**,措辭與第一次完全相同 → 判定為自動規則(luna 不對新訂閱開容量),非區域問題
  - 決定:不再送件;Phase 1 以 gpt-4o 2024-11-20 進行。若需更新模型,今日即有配額、免申請的選項:gpt-5-mini(GlobalStandard 500K)、gpt-5.4-mini(DataZoneStandard 200K),待首次 eval 有 4o 基準後再比較;30 天後若仍要 luna 可重送
  - OpenAI 帳戶留在 eastus2 不搬回(Standard gpt-4o 配額 50K 相同,再搬只多一次刪+purge)
- [x] 4.9 DI custom neural 訓練集上傳(2026-09-30 完成,143 blobs;桌機;`--assemble` 只複製既有 PDF,不渲染,安全):
  ```powershell
  python -m app.datagen.di_labels --assemble check\di_upload
  $st = az storage account list -g rg-claimcrew --query "[0].name" -o tsv     # Bicep 依訂閱自動命名的 storage 帳戶
  az storage container create -n di-training --account-name $st --auth-mode key
  az storage blob upload-batch -d di-training -s check\di_upload --account-name $st --auth-mode key --overwrite
  az storage blob list -c di-training --account-name $st --auth-mode key --query "[].name" -o tsv | Measure-Object -Line
  ```
  預期 blob 數 = 71 PDF + 71 labels.json + 1 fields.json = **143**
- [x] 4.10 DI 訓練(2026-09-30 完成,`diagnosis-cert-v3`;4.11 真實觸發 8/8):
  - 入口:`https://documentintelligence.ai.azure.com` 會轉到 **Content Understanding Studio**,選左邊 **Document Intelligence** 卡片(右邊是另一套 LLM 服務,不用);Custom extraction model → Create a project(`claimcrew` / di-claimcrew / API **2024-11-30 4.0 GA** / storage `$st` 容器 `di-training`,Folder 留空)
  - ✅ 掛號 ② 過關:71 份 PDF 與 8 個欄位的框由 labels.json 自動載入,格式被原樣接受
  - ❌ 首次載入報 **Duplicated labels**:出院日=開立日等同值欄位共用同一框(產生器取第一命中);已修並重生成 71 份標註(commit 見 git log)。**重新上傳只需 json**:
    ```powershell
    git pull origin development
    $st = az storage account list -g rg-claimcrew --query "[0].name" -o tsv     # Bicep 依訂閱自動命名的 storage 帳戶
    az storage blob upload-batch -d di-training -s data\di_training --pattern "*.json" --account-name $st --auth-mode key --overwrite
    ```
    上傳後在 Studio 專案頁重新整理(或關掉專案再開),Duplicated labels 對話框不應再出現
  - ❌ 第一次 Train 立即失敗 `ModelBuildError: Can't find any OCR files for training`:訓練還需要每份 PDF 的 `*.ocr.json`(Layout 結果),只能由 DI 產生 → Label data 頁 **Run layout ▾ → Run layout on all documents**
  - F0 限流:批次 Run layout 會有部分 **429**(每秒約 1 次),屬預期;重跑 **Run layout on unanalyzed documents** 直到 71 份全綠,不需升 S0
  - ✅ Train `diagnosis-cert-v2` Neural **succeeded**(2026-09-30,0.5 訓練小時;F0 允許 neural 訓練,假設過關;Accuracy 欄顯示「-」屬 neural 常態)
- [x] 4.11 DI 模型真實觸發測試(固定解法第 7 條;金鑰只進環境變數,不落檔):
  ```powershell
  $env:DI_KEY = (az cognitiveservices account keys list -n di-claimcrew -g rg-claimcrew --query key1 -o tsv)
  python tools\di_smoke.py C008 --dump check\di_C008.json
  ```
  預期:8 欄位與 claim.json 真值一致(admission/discharge 為第一段)、信心分數印出、版面文字含「第2次」列;不一致以非零退出並貼輸出給雲端 session
  - 2026-09-30 v2 實測:REST 細節首跑即正確;7/8 ✓、信心 0.80~0.97、docConfidence 0.999、「第2次」三列俱在;❌ treatment_summary 只抽到第一句 → 根因是標註產生器跨行退回切段(15/71 份),非 DI 問題
  - 修法:產生器改逐字座標、每行一框(71/71 拼接等於真值);重傳 json → Train **`diagnosis-cert-v3`** Neural
  - ✅ v3 實測:C008 8/8、C055 8/8;信心最低為 treatment_summary 0.67~0.78 與 C055 issue_date 0.67(Gate 低信心門檻調校參考)。**目前使用的模型 ID:`diagnosis-cert-v3`**(v1 失敗、v2 摘要截斷,可刪)
- [x] 4.12 條款入 Search(2026-10-01 完成:索引 104 文件、embedding 12.8s;Free tier 向量欄位 / hybrid 語法 / zh-Hant 分析器三個假設皆過;**首跑 recall@5 0.655、@10 0.835**,漏的集中在名詞定義、應備文件/補正/給付期限、等待期與附錄等「結構性條款」——從文件內容組的查詢搜不到它們):
  ```powershell
  git pull origin development
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew   # 應只多 text-embedding-3-small 部署
  az deployment sub create --name claimcrew-p1 --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew
  $env:SEARCH_KEY = (az search admin-key show --service-name srch-claimcrew -g rg-claimcrew --query primaryKey -o tsv)
  $env:AOAI_KEY   = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
  python tools\search_index.py create
  python tools\search_index.py query "出院後十四日內因同一疾病再次住院" --policy POL-A -k 5
  python tools\search_index.py selftest -k 5
  python tools\search_index.py selftest -k 10
  ```
  量測事實:每張保單全文僅 2,200~2,600 字(約 3.3k tokens),檢索在此規模非必要 → 條款上下文策略改為 **A/B 實驗**(首次真實 eval 兩種各跑一輪定案):
  - A `core+search`:標題規則核心集 ∪ hybrid top-10。初版核心集(16/12/15 條)真實量測 **0.986(70/71,C067 漏時效兩條)**;2026-10-01 人工核定 xlsx(104 條,97v/7x,翻轉皆情境→核心:時效 5 條、B 第11條未列表手術、C 第7條給付總則)後核心集 **18/14/18 條**(1,569/1,429/1,773 字),C067 兩條已入核心 → 預期 71/71,重跑確認:`python tools\search_index.py selftest -k 10 --core`
  - B `full`:整張保單入 prompt,recall 定義上 100%,每案多約 3.3k input tokens

- [x] 4.13 單 agent 端到端全量(2026-10-01;三個金鑰只進環境變數):
  ```powershell
  git pull origin development
  $env:DI_KEY     = (az cognitiveservices account keys list -n di-claimcrew  -g rg-claimcrew --query key1 -o tsv)
  $env:AOAI_KEY   = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
  $env:SEARCH_KEY = (az search admin-key show --service-name srch-claimcrew -g rg-claimcrew --query primaryKey -o tsv)
  python tools\aoai_smoke.py                                                   # schema 變更後必跑(structured outputs 掛號校正點)
  python tools\run_pipeline.py --all --strategy full --out check\pred_full_vN --pace 5   # 72 案約 15 分鐘(DI 快取)、約 $2.2;抽取預設 gpt-4.1-mini(ADR-007),全 4o 對照加 --extraction-deployment gpt-4o
  python tools\cost_report.py check\pred_full_vN\_run.jsonl                             # 逐呼叫 token 與 USD(價目表釘於腳本)
  python -m eval.run_eval check\pred_full_vN > check\eval_full_vN.json
  Compress-Archive check\pred_full_vN check\pred_full_vN_YYYYMMDD.zip -Force   # 上傳給雲端做離線 Gate 重跑 / 門檻格搜
  ```
  - 吞吐事實:gpt-4o Standard 配額上限 50K TPM(Bicep `modelCapacity` 50),單案 prompt ≈ 15.7k tokens → 不可並行;`--pace 5` 為緩衝
  - Gate 為純函式:門檻/規則變更不必重跑 72 案,`python tools\regate.py check\pred_full_vN [--field-threshold 0.70]` 離線重算
  - 歷史:首跑(capacity 10)72 案幾乎全 429 → W11;v1 eval 0.139 / Gate 前 0.778;v2 Gate 前 0.806(見 devlog 2026-10-01)

- [x] 4.14 Foundry Agent Service 探針(SPEC-n,2026-10-02;Entra 驗證,**不用金鑰**)—— 桌機已跑:P1/P3/P4 過、P2 噪音校正後過(EVIDENCE 19.5/19.6);專案已改名 claimcrew(見 4.15):
  ```powershell
  git pull origin development
  pip install -r requirements.txt                                              # 新增 azure-ai-projects 2.7.0 / azure-identity 1.26.0
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true   # 預期:新增 fdy-claimcrew(AIServices)+ 專案 claimcrew-probe + 部署 gpt-4.1-mini;既有 11 資源 NoChange
  az deployment sub create  --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true --name claimcrew-p2n
  $me = az ad signed-in-user show --query id -o tsv
  $proj = "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-claimcrew/providers/Microsoft.CognitiveServices/accounts/fdy-claimcrew/projects/claimcrew-probe"   # 巢狀資源用 az resource show 會 InvalidResourceTypeNameFormat,直接組 id
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Foundry User" --scope $proj   # Agent 建立/呼叫所需;生效約 1–5 分鐘。2026 更名:舊名 "Azure AI User" 會回 Role doesn't exist(2026-10-02 實證)
  az login                                                                     # 新視窗一次;DefaultAzureCredential 走 az CLI 憑證
  $env:FOUNDRY_PROJECT_ENDPOINT = "https://fdy-claimcrew.services.ai.azure.com/api/projects/claimcrew-probe"
  $env:AOAI_KEY = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)   # parity 模式的直呼臂仍用既有 oai 帳戶
  python tools\agent_probe.py check\pred_route_v1 --limit 10 --mode parity --out check\probe_agent_parity.jsonl
  python tools\agent_probe.py check\pred_route_v1 --limit 10 --mode gate   --out check\probe_agent_gate.jsonl
  ```
  - 事前門檻(SPEC-n):P1 parity 10/10 schema 合法;P2 identical ≥ 9/10;P3 第 2 案起 cached_tokens>0 ≥ 5/9;P4 gate 模式 rounds ≤ 2 全數終止(只記錄)
  - 引用形狀已實證(2026-10-02):`agent` 回 400 `The 'agent' property is deprecated. Use 'agent_reference'`,預設已改 `agent_reference`;單案 openai 例外只記該案 error 不中斷
  - 探針結束會刪除自己建的 agent 版本;要在 Portal 看 agent 加 `--keep-agents`
  - 拆除:`az deployment sub create … enableFoundry=false` 不會刪既有資源(Bicep 增量模式),要拆 Foundry 帳戶用 `az cognitiveservices account delete -n fdy-claimcrew -g rg-claimcrew` 再 `purge`

- [x] 4.15 Repair agent(SPEC-o,2026-10-03;Foundry 搬 eastus + gpt-4o;Entra 驗證 + 第二意見用 oai 金鑰)—— ①~④ 已跑:R1/R2 兩組重放否決放行權(EVIDENCE 20)→ ADR-008;⑤ 線上整合依結論不做:
  ```powershell
  git pull origin development
  # ① 拆舊 Foundry 帳戶(eastus2;不可逆,確認後執行):巢狀資源(專案、部署)要先刪,否則 CannotDeleteResource(2026-10-03 實證);
  #    軟刪除會佔住名稱,必須 purge 才能同名重建
  az cognitiveservices account deployment delete -n fdy-claimcrew -g rg-claimcrew --deployment-name gpt-4.1-mini
  az resource delete --ids "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-claimcrew/providers/Microsoft.CognitiveServices/accounts/fdy-claimcrew/projects/claimcrew-probe"
  az cognitiveservices account delete -n fdy-claimcrew -g rg-claimcrew
  az cognitiveservices account purge  -n fdy-claimcrew -g rg-claimcrew -l eastus2
  # ② 重建於 eastus(gpt-4o 2024-11-20 Standard 50 + gpt-4.1-mini 50;專案名改 claimcrew)
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true
  az deployment sub create  --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true --name claimcrew-p2o
  $me = az ad signed-in-user show --query id -o tsv
  $proj = "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-claimcrew/providers/Microsoft.CognitiveServices/accounts/fdy-claimcrew/projects/claimcrew"
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Foundry User" --scope $proj   # 等 1–5 分鐘
  # ③ 每個新視窗
  $env:FOUNDRY_PROJECT_ENDPOINT = "https://fdy-claimcrew.services.ai.azure.com/api/projects/claimcrew"
  $env:AOAI_KEY = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
  # ④ 離線重放(先 3 案驗高風險假設 1,再全量;v8 與 route_v1 兩組都要)
  python tools\rerepair.py check\pred_full_v8  --out check\pred_full_v8_repaired  --limit 3
  python tools\rerepair.py check\pred_full_v8  --out check\pred_full_v8_repaired
  python tools\rerepair.py check\pred_route_v1 --out check\pred_route_v1_repaired
  python -m eval.run_eval check\pred_full_v8_repaired  > check\eval_full_v8_repaired.json
  python -m eval.run_eval check\pred_route_v1_repaired > check\eval_route_v1_repaired.json
  Compress-Archive check\pred_full_v8_repaired,check\pred_route_v1_repaired check\pred_repaired_YYYYMMDD.zip -Force
  # ⑤ 線上整合(T1–T4 過後才用):run_pipeline 加 --repair(預設修補/第二意見部署 gpt-4o)
  python tools\run_pipeline.py --all --strategy full --out check\pred_full_vN --pace 5 --repair
  ```
  - 事前門檻(SPEC-o):T1 Gate 後 ≥ 0.75、T2 放行金額 100% 對、T3 危險錯 0、T4 真值人工被放行 0;兩組都要過,任一不過退回方案 P
  - 成本:約 63 案次 × 2 呼叫 4o(快取率高)+ 約 10 件第二意見 ≈ 4.5 USD
  - 探針 4.14 的指令以後請改用本節 endpoint(專案名 claimcrew-probe → claimcrew)

- [x] 4.16 方案 P + Repair 顧問模式(SPEC-p,2026-10-03;ADR-008)—— 八組離線量測完成(EVIDENCE 21);`--repair` 顧問模式**未做過線上全量**(非必要,Job 預設不帶):
  ```powershell
  git pull origin development
  python tools\regate.py check\pred_route_v1                           # 離線:P2 自動拒付 + P3 分段限額 + SPEC-q 收據覆蓋檢核已內建;P1 合併預設開,--no-merge-split-lines 關閉對照
  python tools\run_pipeline.py --all --strategy full --out check\pred_full_vN --pace 5 --repair   # --repair 現為顧問模式:只寫 repair 建議,不改判定
  python tools\rerepair.py check\pred_full_vN --out check\pred_full_vN_adv              # 離線補顧問建議(預設顧問;--release 僅量測)
  python -m eval.run_eval check\pred_full_vN > check\eval_full_vN.json
  ```
  - 八組離線量測(雲端,0 成本):P2+P3 每組 accuracy 皆升、自動拒付 40/40 真值 deny、危險錯 0、放行錯不增;P1 在 v7 C069 露洞 → 預設關
  - SPEC-q(2026-10-03)收據覆蓋檢核 `receipt_category_uncovered`(POL-C approve:收據命中第8~11條類別須有對應明細行,附錄一可覆蓋第9條)補上 C069 漏行洞 → 八組重測 P1 開啟後放行錯 0、誤旗標 0,**P1 改預設開**
  - 顧問建議讀法:`repair.would_release` 為 True 且 `repair.repaired_decision/amount` 即 AI 建議;`applied` 恆 False

- [x] 4.17 Logic Apps 人工覆核卡片(SPEC-r,2026-10-03;Outlook.com 連接器、只送 manual_review;**已部署並真寄成功**):
  ```powershell
  git pull origin development
  # 1. 先離線預覽(0 成本、不打網路):36 張 HTML 到 check\review_html,開一張確認版面與無姓名
  python tools\send_review.py check\pred_full_v8 --dry-run-dir check\review_html
  # 2. 部署(what-if 應只多 2 資源:logic-claimcrew-review + outlook-claimcrew)
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=<你的 outlook.com 信箱>
  az deployment sub create  --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=<你的 outlook.com 信箱>
  # 3. Portal 授權連線(Bicep 做不到的一步):rg-claimcrew → API 連線 outlook-claimcrew → 編輯 API 連線 → 授權 → 以 outlook.com 帳號登入 → 儲存
  # 4. 取觸發 URL(含 sig= 的 SAS 簽章 = 秘密;只放環境變數,不貼進對話/檔案)
  $rid = az resource show -g rg-claimcrew -n logic-claimcrew-review --resource-type Microsoft.Logic/workflows --query id -o tsv
  $env:REVIEW_WEBHOOK_URL = az rest --method post --uri "$rid/triggers/manual/listCallbackUrl?api-version=2019-05-01" --query value -o tsv
  # 5. R-M2 真送 1 案(建議 C055 或任一 manual_review 案)→ 信箱收到、卡片可讀 → 截圖 docs\screenshots\20261003_review_card.png
  python tools\send_review.py check\pred_full_v8 C003 --limit 1
  # 6. R-M3 真實觸發失敗路徑(固定解法 7):把 URL 最後一碼改掉再送 1 案 → 期望 "error http=401/403"、exit 1、不拋例外;改回後再送 1 案成功
  $env:REVIEW_WEBHOOK_URL = $env:REVIEW_WEBHOOK_URL.Substring(0, $env:REVIEW_WEBHOOK_URL.Length - 1) + "x"
  python tools\send_review.py check\pred_full_v8 C003 --limit 1
  # 7. 全量時併用(SPEC-s 後為摘要):每案 Gate 後收集(run log review=queued/skipped),跑完一封摘要;無 --review 時恆 off、輸出位元組不變
  python tools\run_pipeline.py --all --strategy full --out check\pred_full_vN --pace 5 --review
  # 8. SPEC-s 摘要信(2026-10-03;Outlook.com 逐案第 6 封即封鎖 → 一輪一封):先落地 <pred>\_review_digest.html 再寄;--per-case 回到逐案
  python tools\send_review.py check\pred_full_v9 --dry-run-dir check\review_html     # 預覽:1 封、N 案、bytes
  python tools\send_review.py check\pred_full_v9                                       # 真送 1 封(需先解鎖信箱)
  ```
  - 寄信失敗時觸發 POST 同步回 502(工作流末端 Response),管線記 `review=error: HTTP 502 {...error...}` 不中斷(回應本文含 Send 動作錯誤);仍可 `az rest --method get --uri "$rid/runs/<run id>/actions/Send_review_email?api-version=2019-05-01"` 查執行歷程
  - **W14 連線未授權的症狀**:`send_review` 回 `error http=502`、`az resource show … outlook-claimcrew --query properties.statuses` 顯示 `Unauthenticated`;Portal 入口在 API 連線左側選單 **General → Edit API connection → Authorize → Save**(2026-10-03 實測,漏按 Save 不生效)
  - 連線 token 長期未用可能失效(pre-mortem):每次全量前先 `--dry-run-dir` 再真送 1 案確認
  - 卡片不含病患/申請人姓名:LLM 自由文字(relevance / reasoning)中的姓名以 `<被保險人>` 遮罩(v8 C019/C020/C054 實證會寫出姓名)
  - **W15 Outlook.com 批量寄信封鎖**:症狀 `send_review`/run log 連續 `error: HTTP 502`、Send 動作 `code: Forbidden`、連線仍 `Connected`,信箱收到 Outlook.com Team「unusual activity, temporarily blocked」(2026-10-03 v9 全量第 6 封後 16 封全失敗)。解法:①`https://account.live.com/Abuse` 或 account.microsoft.com → 安全性 加手機驗證,網頁信箱手動寄一封給自己確認可寄;②**解除後連線 token 仍失效**(`AADSTS70000 … service abuse mode`,連線 `statuses` 顯示 refresh 失敗)→ Portal `outlook-claimcrew` → Edit API connection → Authorize → Save 重新授權;③之後一律用摘要信(SPEC-s 預設),逐案寄送只在單案測試用。2026-10-04 實測:解除 + 重授權後 v9 摘要 1 封 22 案 `sent http=200`

- [x] 4.18 Container Apps Job 編排上雲(SPEC-t,2026-10-04;無金鑰:使用者指派身分 + RBAC;映像 tag = git SHA)—— 1~6 全過(cloud1 `20261004T0647-3b5e5c8`、cloud2 `20261004T0906-c9b2fca`、T-M4 撤角色 3 秒 401 Failed → 恢復 diag 全綠),T-M5 成本 10/4 實帳 ≈NT$178、Container Apps 計量 0 → **SPEC-t 全部結案**:
  ```powershell
  git pull origin development
  # 0. T-M1 已過(10/04):di_smoke / aoai_smoke --entra 皆成功;個人帳號已有 DI / AOAI 資料面角色
  # 1. 先部署基礎(ACR + 身分 + RBAC + 環境;imageTag 留空 → 不建 Job。W16:Job 建立時 ARM 驗證 manifest,映像未推會 MANIFEST_UNKNOWN 整段失敗)
  $sha = (git rev-parse --short=7 HEAD)
  $mail = "你的覆核信箱@outlook.com"   # 與 4.17 部署時相同值;禁止直接寫 <信箱>:PowerShell 把 < 當檔案重導向,整條指令不執行且只回「The system cannot find the file specified」
  az deployment sub create --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=$mail enableJob=true
  $acr = az deployment sub show -n main --query properties.outputs.acrLoginServer.value -o tsv   # acr<...>.azurecr.io
  # 2. 雲端建映像(不需本機 Docker;約 3~5 分鐘),再部署一次帶 imageTag 建 Job
  az acr build -r $acr.Split('.')[0] -t "claimcrew:$sha" --build-arg GIT_SHA=$sha .
  az deployment sub create --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=$mail enableJob=true imageTag=$sha
  # 3. DI 快取上傳(F0 月額度 500 頁;一輪 213 頁,沒快取一個月只能跑兩輪);個人帳號需 Storage Blob Data Contributor
  $st = az deployment sub show -n main --query properties.outputs.storageAccountName.value -o tsv
  $me = az ad signed-in-user show --query id -o tsv
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Storage Blob Data Contributor" --scope (az storage account show -n $st -g rg-claimcrew --query id -o tsv)
  $env:CLAIMCREW_STORAGE_ACCOUNT = $st
  python tools\sync_cache.py push check\di_cache                      # 期望:上傳 213 個新檔
  # 4. 等 5 分鐘(RBAC 傳播)再啟動 Job;追執行狀態與日誌
  az containerapp job start -n caj-claimcrew-pipeline -g rg-claimcrew
  az containerapp job execution list -n caj-claimcrew-pipeline -g rg-claimcrew -o table
  az containerapp job logs show -n caj-claimcrew-pipeline -g rg-claimcrew --container pipeline --follow
  # 5. 取回輸出並 eval(runs/<run_id>/ 下 72 份 json + _run.jsonl + run_meta.json + _review_digest.html)
  az storage blob list --account-name $st -c runs --auth-mode login --prefix runs/ --query "[].name" -o tsv | Select-String run_meta
  az storage blob download-batch --account-name $st -s runs --auth-mode login --pattern "runs/<run_id>/*" -d check\cloud_runs
  python -m eval.run_eval check\cloud_runs\runs\<run_id> | Out-File -Encoding utf8 check\eval_cloud_<run_id>.json
  # 6. T-M4 真實觸發失敗路徑:撤掉 Job 身分的 **OpenAI** 角色(DI 有快取不會被呼叫,撤 DI 角色測不到)→ start → 期望 Failed、run_meta status=failed、error 含 401;恢復後再成功一次
  #    變數不可叫 $pid(PowerShell 唯讀內建變數,賦值失敗後 delete 會拿 shell 的 process id 去查,2026-10-04 實踩、角色沒撤成白跑一輪)
  $jobId = az identity show -n id-claimcrew-job -g rg-claimcrew --query principalId -o tsv
  $oai   = az cognitiveservices account show -n oai-claimcrew -g rg-claimcrew --query id -o tsv
  az role assignment delete --assignee $jobId --role "Cognitive Services OpenAI User" --scope $oai
  az role assignment list --assignee $jobId --scope $oai -o table     # 必須為空才算撤成
  az containerapp job start -n caj-claimcrew-pipeline -g rg-claimcrew
  az role assignment create --assignee-object-id $jobId --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" --scope $oai
  ```
  - 之後每次改程式要上雲:`$sha` 重算 → `az acr build --no-logs`(W17)→ `az deployment … imageTag=$sha`(只會 modify Job 的 image)→ **先 `az containerapp job show -n caj-claimcrew-pipeline -g rg-claimcrew --query properties.template.containers[0].image -o tsv` 確認尾巴是新 tag** → `job start`(2026-10-04 部署指令靜默失敗、Job 仍指舊映像就啟動,白跑一次後 `job stop --job-execution-name <名>` 停掉)
  - **自檢模式**:`job start --args` 吃不下 `--diag`(`--` 開頭值被當選項),改用環境變數觸發;覆寫啟動**必帶 `--image <acr>/claimcrew:<sha>`**(W18:不帶會回 `ContainerAppImageRequired`,覆寫模式不繼承範本映像);且 `--env-vars` 會**整組取代**既有環境變數,必須全列:`az containerapp job start -n caj-claimcrew-pipeline -g rg-claimcrew --image acrclaimcrew5plo5lmu3rgxc.azurecr.io/claimcrew:$sha --env-vars CLAIMCREW_AUTH=entra CLAIMCREW_STORAGE_ACCOUNT=$st AZURE_CLIENT_ID=<身分 clientId> GIT_SHA=$sha CLAIMCREW_DIAG=1 CLAIMCREW_HOLD=300`;覆寫啟動的執行其容器名是 `caj-claimcrew-pipeline`,範本啟動的才是 `pipeline`(`logs show --container` 要跟著換);`logs show --follow` 最多 10 分鐘;主控台日誌進 Log Analytics 有數分鐘延遲
  - 首次 Job 執行曾 9 秒 Failed、無容器啟動事件(2026-10-04),之後未重現;再遇到先 `job execution list`,再查 Log Analytics `ContainerAppSystemLogs_CL`
  - `download-batch` 的 `-d` 目錄必須已存在;`eval` 用 `python -m eval.run_eval check\cloud_runs\runs\<run_id>`
  - 拆建:ACR 隨 `az group delete` 一起刪,重建照 1~4 全跑;映像不保留(repo 可重生)

- [x] 4.19 Content Safety 影子模式(SPEC-v,2026-10-05;F0;groundedness 三輪量測否決為路由訊號,見 EVIDENCE 27;影子欄位程式保留,Job 是否續開 `--safety` 依內部開發規則(未公開)待辦):
  - **第二段 E1~E3 已執行(10/05,EVIDENCE 28、ADR-009 accepted)**;**已部署 `enableContentSafety=false`(10/05 14:17 UTC Succeeded;Job args 僅 `--review --pace 5`、`CS_ENDPOINT` 已移除)**;增量部署不刪資源,`cs-claimcrew`(F0)與其上 Job 角色仍在,留待拆建時一起刪(Job 移除 `--safety` 與 CS 角色;CS 資源 F0 可一併消失,探針 `cs_smoke`/`cs_qna_probe` 需要時再開);E3-b 補件:`Compress-Archive` 同名檔會互相覆蓋,三個 `C003X.json` 先改名(`Copy-Item check\pred_injection_en\C003X.json check\C003X_en.json` 等)再壓
  ```powershell
  git pull origin development
  # 1. 部署 Content Safety(+ Job 角色 / CS_ENDPOINT / --safety;imageTag 沿用現行,之後程式改動再重建映像)
  $mail = "你的覆核信箱@outlook.com"
  az deployment sub create --location eastus --template-file infra\bicep\main.bicep --parameters baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=$mail enableJob=true imageTag=<現行sha> enableContentSafety=true
  #    W19:若回 RoleAssignmentExists,是先前手動 az role assignment create 的隨機 GUID 與 Bicep 決定性 guid 衝突 → 刪手動那筆再部署
  # 2. 個人帳號資料面角色(smoke / resafety 用);等 3 分鐘
  $me = az ad signed-in-user show --query id -o tsv
  $cs = az cognitiveservices account show -n cs-claimcrew -g rg-claimcrew --query id -o tsv
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Cognitive Services User" --scope $cs
  # 3. 假設 1/3:一次真呼叫(期望 groundedness: OK + analyze: OK;FAIL 含 403/SKU/not supported 字樣 = F0 不支援,停下回報、不自行改 S0)
  $env:AZURE_TOKEN_CREDENTIALS = "dev"   # W20:桌機跳過託管身分探測,否則 DefaultAzureCredential 先等 IMDS 逾時(看起來像卡住)
  python tools\cs_smoke.py C003 --entra
  python tools\cs_smoke.py C003 --entra --grid --control   # 假設 2 診斷:control 的 copied+fabricated 應 0.5,回 0/1 = 不切中文句
  # 4. 假設 2:10 組歷史輸出離線補跑(每組 ≤72 筆、F0 月額度 5,000 筆;先 --limit 5 看格式再全量)
  $env:CLAIMCREW_AUTH = "entra"
  python tools\resafety.py check\pred_full_v9 --limit 5
  python tools\resafety.py check\pred_full_v9
  # 5. 量測:eval 自動併入 sidecar,報告多一個 groundedness 區塊(依 final_decision 分群 median / p90 / ungrounded 數)
  python -m eval.run_eval check\pred_full_v9 | Out-File -Encoding utf8 check\eval_v9_safety.json
  Compress-Archive check\pred_full_v9\*.safety.json check\safety_v9.zip -Force   # 連同 eval_v9_safety.json 給雲端 session
  ```
  - 線上一輪:`$sha` 重算 → `az acr build --no-logs` → 帶 `imageTag=$sha enableContentSafety=true` 部署 → `job show … image` 確認 → `job start`;摘要信目錄會多一欄 ungrounded
  - **第二段(ADR-009 證據補強,2026-10-05)**:
    ```powershell
    $env:CLAIMCREW_AUTH = "entra"; $env:AZURE_TOKEN_CREDENTIALS = "dev"
    # E1 重複性(10 案 × 3 次,寫 C###.safety.rep{1,2,3}.json,不碰正式 sidecar)
    python tools\resafety.py check\pred_full_v9 C003 C027 C032 C001 C008 C014 C024 --repeat 3 --tag rep
    python tools\resafety.py check\cloud_runs\runs\20261004T0647-3b5e5c8 C066 C044 C049 --repeat 3 --tag rep
    # E2 QnA 逐主張(同 10 案 × ≤3 主張 × 3 次)
    python tools\cs_qna_probe.py check\pred_full_v9 C003 C027 C032 C001 C008 C014 C024 --out check\qna_v9.json
    python tools\cs_qna_probe.py check\cloud_runs\runs\20261004T0647-3b5e5c8 C066 C044 C049 --out check\qna_cloud1.json
    # E3-a Prompt Shields smoke(良性 + 三種注入樣式)
    python tools\cs_smoke.py C003 --entra --shield
    # E3-b 注入端到端(每樣式:建變體 → 跑管線 → 比對 C003 基線;DI 各 1~3 頁、LLM 約 USD 0.03/樣式)
    foreach ($s in "en","zh","note") {
      python tools\injection_probe.py C003 --style $s
      python tools\run_pipeline.py C003X --cases-dir check\injection --safety --out check\pred_injection_$s
    }
    Compress-Archive check\pred_full_v9\*.rep*.json, check\cloud_runs\runs\20261004T0647-3b5e5c8\*.rep*.json, check\qna_*.json, check\pred_injection_*\C003X.json check\adr009_evidence.zip -Force
    ```

## 5. 什麼情況下什麼指令(速查)

| 情況 | 指令 / 動作 |
|---|---|
| 雲端 session 說「已推送」 | `git pull origin development` |
| pull 後測試掛掉 | 先看是不是 `requirements*.txt` 變了 → 重裝;再貼錯誤給雲端 session |
| `az` 指令回 401 / token expired | `az login --tenant $tenant`(見 W6;`$tenant` 由 `check\env.ps1` 載入) |
| 要看目前訂閱花了多少 | Portal「成本分析」,或 `az consumption usage list`(待驗證) |
| 想重生成資料 / PDF | **不要在桌機做**,告訴雲端 session |
| 想加案子 / 改真值 | 雲端 session 出提案總表 → 我逐案勾 → 雲端 session 生成 |
| 桌機 `git status` 出現一堆 PDF 變動 | 代表誤跑了渲染;`git checkout -- data/` 還原 |

## 6. 桌機環境坑(踩過一次就記,格式同內部開發規則的雲端坑)

**W1. `pip install` 報 `No matching distribution found for pytest==9.0.3`,列出的最新版只到 8.x**
- 根因:`python` 指到系統舊版 Python 3.9,不是 3.11
- 解法:`Remove-Item -Recurse -Force .venv; py -3.11 -m venv .venv`,重新啟用後 `python --version` 確認 3.11.x
- 反例:不要去降 requirements 的版本

**W2. `pip` 每個套件先失敗五次 `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain` 再成功**
- 根因:桌機網路有 TLS 攔截(防毒/公司 proxy),pip 不認其憑證;重試最後會過,只是慢
- 解法(可選):匯出攔截方根憑證為 .pem → `pip config set global.cert <路徑>`;**禁止**用 `--trusted-host` 關驗證
- 現況:2026-09-30 先靠重試撐過,未處理

**W3. `bash tools/gauntlet.sh` 報 `set: pipefail: invalid option`**
- 根因:Windows 上的 `bash` 不是完整 Linux shell
- 解法:桌機不跑 gauntlet.sh,改跑第 1 節的四連指令(等價)

**W5. `az bicep build` 在新版 CLI 冒 `BCP318: The value of type "module | null" may be null`**
- 根因:條件式模組(`if (enablePhase2)`)的 outputs 直接取值,新版 linter 視為可能 null;舊版 0.30 不報
- 解法:改用安全取值 `mod.?outputs.x ?? ''`;同旗標模組間傳參用 `mod!.outputs.x`(已修,2026-09-30)
- 驗證:0.30.23 與 0.47.16 皆 0 warnings

**W4. `winget install` 後新指令找不到**
- 根因:PATH 只在新開的視窗生效
- 解法:開新 PowerShell,重新 `cd` + 啟用 venv

**W6. `az login` 報 `AADSTS50076 ... must use multi-factor authentication`,接著 `No subscriptions found`**
- 根因:Pay-As-You-Go 目錄強制 MFA;不帶 tenant 的 `az login` 走輕量列舉,不觸發 MFA,該租戶驗證失敗才顯示沒訂閱(訂閱其實存在)
- 解法:`az login --tenant $tenant`,瀏覽器完成 MFA 後選訂閱 1
- 驗證:`az account show --query "{sub:id, tenant:tenantId, name:name}" -o table` 印出 claimcrew
- 反例:不要去 Portal 找「為什麼沒訂閱」,也不要重建訂閱

**W7. `what-if` / `create` 回 `ServiceModelDeprecating: The model ... is in deprecating state and cannot be used for new deployments`**
- 根因:Phase 0 依訓練知識釘的 gpt-4o 2024-08-06 / 4o-mini 2024-07-18 已進入 Deprecating,Azure 拒絕新部署(既有部署可續用,新建不行);`model list` 預設欄位看不出狀態
- 解法:跑 4.6 的 `model list` 加 `lifecycleStatus` 欄,只選 `GenerallyAvailable` 或 `Legacy`;再跑 `usage list` 確認該 SKU 配額 > 0;改 `infra/bicep/modules/openai.bicep` 的版本字串
- 驗證:`az deployment sub what-if` 不再出現 InvalidTemplateDeployment
- 反例:不要申請 GlobalStandard 配額來救 Deprecating 版本,兩者是獨立問題(2026-09-30 同時踩到)

**W8. `az ... --query "length(@)"` 報 `invalid jmespath_type value: 'length(@'`,接著冒 `Failed to load python executable`**
- 根因:Windows 的 `az` 是 `.cmd` 批次檔,參數裡的 `)` 被 cmd 當成區塊結尾截斷;單引號、雙引號都救不了;第二行是包裝器失敗後的雜訊,不是 Python 壞了
- 解法:JMESPath 避免括號——計數改 `--query "[].name" -o tsv | Measure-Object -Line`,篩選改用 `[?...]` 不含函式呼叫
- 驗證:印出整數
- 反例:不要去重裝 Azure CLI 或檢查 Python 路徑;不要改引號(2026-09-30 單雙引號皆實測失敗)

**W9. `git push` 印 `This repository moved. Please use the new location: .../ClaimCrew.git`**
- 根因:GitHub repo 於 2026-09-30 由 `claimcrew` 改名 `ClaimCrew`(統一專案命名),桌機 remote 仍記舊名;GitHub 自動轉向所以能推,但若日後同名新 repo 出現轉向即失效
- 解法:`git remote set-url origin https://github.com/seancpc/ClaimCrew.git`;本機資料夾一併改名(Windows 不區分大小寫,需兩段改名:`Rename-Item C:\claimcrew C:\claimcrew_tmp; Rename-Item C:\claimcrew_tmp C:\ClaimCrew`,改名前先關閉該資料夾內的終端機與編輯器,Windows 路徑不分大小寫,venv 內嵌的絕對路徑仍可解析,不用重建)
- 驗證:`git remote -v` 顯示新網址;`git pull origin development` 不再出現 moved 提示;`.\.venv\Scripts\Activate.ps1` 後 `python -c "import app"` 正常

**W10. `Get-Content` 印出的中文 JSON 全是「鋡思??芯」亂碼**
- 根因:PowerShell 主控台輸出編碼非 UTF-8,`Get-Content` 預設也不以 UTF-8 讀檔;檔案本身正確
- 解法:同一視窗先 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`,讀檔加 `-Encoding UTF8`:
  `Get-Content -Encoding UTF8 check\pred_full\C008.json`;要永久生效可在 PowerShell 設定檔加同一行
- 驗證:`Get-Content -Encoding UTF8 data\claims\C008\truth.json` 看得到中文

**W11. 全量跑 72 案幾乎每案 `429 ... exceeded token rate limit`,只有缺件案 ok**
- 根因:gpt-4o 部署 capacity 10(= 10K TPM),單案一次呼叫就 ~14k~16k tokens,連一分鐘窗都塞不進;舊重試 1/2/4 秒即放棄且每次重試照燒配額
- 解法:①Bicep `modelCapacity` 50(eastus2 Standard 配額上限)重佈署,`az cognitiveservices account deployment show ... --query sku` 看到 `"capacity": 50`;②程式已改聽 `Retry-After`、6 次、封頂 60 s;③`--pace 5`
- 驗證:重跑全量 `完成 72 / 失敗 0`;C017 一次 429 被退避吸收(95 s)而非失敗
- 反例:不要先懷疑網路或金鑰;what-if 列出的 Cosmos/Search/Storage 一堆 `- Delete` 屬性是 what-if 噪音(Bicep 未宣告的伺服器端預設值),只看 `sku.capacity` 那一筆

**W12. PowerShell 跑 `git filter-branch --env-filter '<多行>'` 回 `fatal: bad revision 'Chuang; export ...'`**
- 根因:PowerShell 5.1 傳遞含空白與雙引號的多行單引號字串給原生程式時會剝掉雙引號,`Sean Chuang` 被拆成兩個參數;git 什麼都沒改(作者仍三行、diff 空)
- 解法:把 env-filter 寫成腳本檔(放 `check\`,已 gitignore),以 Git Bash 路徑 source:
  `git filter-branch -f --env-filter '. /c/ClaimCrew/check/fix_author.sh' -- development main`
- 驗證:`git log development main --format='%an <%ae> | %cn <%ce>' | Sort-Object -Unique` 只有一行;`git diff --stat <舊SHA> development` 為空;推送用 `--force-with-lease=<分支>:<舊SHA>`
- 反例:`git log --all` 仍會看到舊作者,那是 `refs/original/*` 備份與未更新的 `origin/*`,不是改寫失敗;推完 `git for-each-ref --format='%(refname)' refs/original/ | ForEach-Object { git update-ref -d $_ }`

**W13. `az … > check\file.json` 後 python 讀取回 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0`;`az --query "[?contains(name,'x')].{…}"` 回 `].{name:name was unexpected at this time.`**
- 根因:①PowerShell 5.1 的 `>` 重導向預設寫 **UTF-16 LE 含 BOM**(0xFF 0xFE),不是 UTF-8;②`az` 是 `az.cmd` 批次檔,PowerShell 把含 `[`、`(`、`,`、`{` 的 `--query` 字串交給 cmd 解析時被拆爛
- 解法:①輸出改 `az … -o json | Out-File -Encoding utf8 check\file.json`,python 用 `encoding='utf-8-sig'` 讀(保險吃掉 BOM);或既有檔案直接 `encoding='utf-16'` 讀;②複雜篩選不用 `--query`,整份存 json 後用 python 過濾(2026-10-02 `list-models` / `usage list` 兩次實證)
- 驗證:`python -c "import json;json.load(open('check/file.json',encoding='utf-8-sig'))"` 無例外
- 反例:不要去改 `$OutputEncoding` 或 chcp,治標且影響整個 session;也不要在 `--query` 外再包一層引號嘗試,cmd 層一樣會拆

**W17. `az acr build … | Select-String` 在建置日誌途中 `UnicodeEncodeError: 'cp950' codec can't encode`,但 ACR 上映像其實已建成功**
- 根因:`az` 的 Python 把 Docker 建置日誌(含非 cp950 字元)寫到 PowerShell 5.1 的 cp950 主控台;管線化後 `Select-String` 迫使逐行解碼,撞到即崩,但雲端建置在 ACR 端早已獨立完成
- 解法:`az acr build --no-logs …` 不串流日誌(需要看日誌時 `az acr task logs -r <acr> --run-id <id>`);或先 `$env:PYTHONIOENCODING="utf-8"` 再跑
- 驗證:`az acr repository show-tags -n <acr> --repository claimcrew -o tsv` 看到 `<sha>` 即為成功,不以主控台有無崩潰判定
- 反例:不要重跑建置「補救」——映像已在,重跑只是多付建置分鐘;也不要 chcp 65001(W10 同理)

**W18. `az containerapp job start … --env-vars …` 回 `(ContainerAppImageRequired) Container with name 'caj-claimcrew-pipeline' must have an 'Image' property specified.`**
- 根因:帶任何覆寫參數(`--env-vars` / `--args` / `--command`)啟動時,CLI 以「覆寫範本」模式送出整個容器定義,不繼承 Job 範本的 image,也不繼承環境變數
- 解法:同時給 `--image acrclaimcrew5plo5lmu3rgxc.azurecr.io/claimcrew:<sha>` 與**完整**環境變數組(CLAIMCREW_AUTH / CLAIMCREW_STORAGE_ACCOUNT / AZURE_CLIENT_ID / GIT_SHA + 本次要加的);不帶任何覆寫參數的 `job start` 則完全用範本
- 驗證:`job execution list` 新一列 Running;覆寫啟動的容器名是 `caj-claimcrew-pipeline`(範本啟動才是 `pipeline`),`logs show --container` 要跟著換
- 反例:不要去改 Bicep 範本加 CLAIMCREW_DIAG——那會讓每次正式跑都進 diag 模式

**W19. `az deployment … create` 回 `RoleAssignmentExists … The ID of the existing role assignment is <GUID>`**
- 根因:T-M4 之類的手動 `az role assignment create` 會產生**隨機 GUID** 名稱的指派;Bicep 的指派名是 `guid(資源, 身分, 角色)` 決定性值,同一組(身分, 角色, 範圍)Azure 只允許一筆 → Bicep 建不出自己那筆
- 解法:`az role assignment delete --ids "<資源id>/providers/Microsoft.Authorization/roleAssignments/<該 GUID>"` 後重跑部署(Job 未執行時空窗無影響);或日後恢復角色一律用「重跑部署」而非手動 create
- 驗證:部署 `provisioningState: Succeeded`,`outputResources` 含該資源的 roleAssignments
- 實例:2026-10-05 SPEC-v 首次部署,衝突的是 10/4 T-M4 手動恢復的 OpenAI User(`b251e80e…`)

**W20. 桌機 `--entra` 工具看起來卡住數十秒,Ctrl+C 後堆疊停在 `azure/identity/_credentials/imds.py` 的 `_request_token`**
- 根因:`DefaultAzureCredential` 依序嘗試 Environment → Workload Identity → **Managed Identity(IMDS 169.254.169.254)** → … → Azure CLI;桌機沒有 IMDS,要等連線逾時才輪到 az login 身分。不是程式錯、也不是網路壞
- 解法:`$env:AZURE_TOKEN_CREDENTIALS = "dev"`(azure-identity 1.26,只用開發者憑證鏈,跳過託管身分;2026-10-05 實測 `cs_smoke --entra` 4 秒完成);或單純等它逾時
- 驗證:工具第二行以內印出結果,無 imds 堆疊
- 反例:不要去改 `auth.py` 的憑證鏈——雲端 Job 就是靠託管身分,改了會把雲端弄壞

- [x] 4.20 SPEC-x 可確定金額桌機實測(判準見 EVIDENCE 30,事前已提交;約 NT$75)—— v10 已跑:模型原判 6/6 approve、欄位 6/6 空 → 撤回 prompt 規則(EVIDENCE 30):
  ```powershell
  git pull origin development
  $env:AZURE_TOKEN_CREDENTIALS="dev"
  foreach ($i in 1..3) { python tools\run_pipeline.py C041 C064 --strategy full --out check\pred_x_rep$i }
  python tools\run_pipeline.py --all --strategy full --out check\pred_full_v10 --pace 5
  python -m eval.run_eval check\pred_full_v10 > check\eval_v10.json
  Compress-Archive check\pred_x_rep1, check\pred_x_rep2, check\pred_x_rep3, check\pred_full_v10, check\eval_v10.json check\specx_evidence.zip -Force
  ```
  - 資料夾整個壓縮會保留資料夾名,不會有同名檔互相覆蓋的問題
  - 順便在全國法規資料庫確認保險法施行細則第7條原文

- **W21 `git pull` 回 `untracked working tree files would be overwritten by merge` → `Aborting`,後續指令照跑舊程式**(2026-10-05)
  - 根因:截圖先存到桌機 `docs/screenshots/`(未追蹤),雲端又把同名檔提交進 repo;PowerShell 不因前一行失敗而停止
  - 解法:本地檔先移到備份資料夾再 pull(repo 內已有同名檔,內容相同則可直接刪備份)
    ```powershell
    New-Item -ItemType Directory -Force check\screenshots_bak | Out-Null
    Move-Item docs\screenshots\20261005_E3c*.png check\screenshots_bak\
    git pull origin development
    ```
  - 防呆:實測前一律 `git pull origin development; if ($LASTEXITCODE) { throw "pull 失敗" }`,並以 `git log --oneline -1` 對照預期 commit
  - 2026-10-06 起 `run_pipeline` 開頭印「程式版本:<短 SHA>[+dirty]」並寫入 `_run.jsonl` 每列 `code_version`;回報結果時一併貼上該行
  - 驗證:`git status` 乾淨、`git log --oneline -1` 為最新;輸出的 `adjudication` 含 `undisputed_amount`

- **W22 新建 Log Analytics 上部署告警規則回 `Operator source expression should be table or column. A semantic error occurred`**(2026-10-08 ⑧ 重建)
  - 根因:查詢引用的 `ContainerAppConsoleLogs_CL` / `ContainerAppSystemLogs_CL` 要等首筆 Container Apps 日誌寫入才建表;ARM 建規則時驗證查詢即失敗
  - 解法:Bicep 分兩段——先 `enableJobAlert=false` 部署、Job 跑過一次後再 `enableJobAlert=true` 重部署(runbook 4.23 步驟 8)
  - 反例:`skipQueryValidation: true` 實測無效(同錯),不要再試
- **W23 Container Apps 環境回 `ManagedEnvironmentCapacityHeavyUsageError` / `AKSCapacityHeavyUsage`**(2026-10-08)
  - 根因:Azure 該區無容量建新叢集(與程式無關;同 Bicep 10/4 在 eastus 成功)
  - 解法:`containerAppsLocation` 參數改區(預設已改 eastus2);改區前先刪掉原區 Failed 的同名環境 `az resource delete -g rg-claimcrew -n cae-claimcrew --resource-type Microsoft.App/managedEnvironments`
  - 驗證:`az resource list -g rg-claimcrew -o table | Select-String "cae-"` 顯示新區 Succeeded
  - 反例:等 70 分鐘重試仍同錯,不要空等
- **W24 PowerShell 下 `az ... --query "[?...]"` / `length(@)` 回 `was unexpected at this time`**(2026-10-08)
  - 根因:`az` 在 Windows 是 `az.cmd`,參數再經 cmd 解析,方括號與括號被吃掉
  - 解法:改 `-o table | Select-String <關鍵字>` 篩選;或在 az 參數前加 `--%` 停止 PowerShell 解析
- **W25 部署 Succeeded,但輸出 `parameters.reviewEmailTo.value` 為 `""`;摘要信不再寄達**(2026-10-10 SPEC-aj 線上部署)
  - 根因:新開的 PowerShell 視窗沒設 `$mail`,`$p` 內 `reviewEmailTo=` 為空;Bicep 舊版不檢查 → Logic App 收件人 `To` 被覆寫為空(監控模組則改為不部署告警,既有告警因增量模式保留)
  - 解法:用 A 區一鍵區(自動載入 `check\env.ps1` 並印 `mail=[…]`)後重新部署;Bicep 已加守門變數 `reviewEmail`(開啟 `enableReview` 或 `enableJobAlert` 且收件人為空 → `fail()`,CI `bicep_check` ⑥ 守住)
  - 驗證:部署指令帶 `--query "properties.parameters.reviewEmailTo.value" -o tsv` 印出信箱;`az resource show -g rg-claimcrew -n logic-claimcrew-review --resource-type Microsoft.Logic/workflows --query "properties.definition.actions" -o json | Select-String "To"` 印出信箱
  - 反例:不要只看 `provisioningState: Succeeded` 判斷部署正確;空字串是合法參數值,部署不會報錯

- [x] 4.21 SPEC-y C073 桌機實測(判準見 EVIDENCE 32,事前已提交;約 NT$75)—— v12 已跑:C072 扣錯桶放行 → 撤回、補 SPEC-z 後 9a 定案(EVIDENCE 32–34):
  ```powershell
  git pull origin development; if ($LASTEXITCODE) { throw "pull 失敗" }
  git log --oneline -1
  foreach ($i in 1..3) { python tools\run_pipeline.py C073 --strategy full --out check\pred_y_rep$i }
  python tools\run_pipeline.py --all --strategy full --out check\pred_full_v12 --pace 5
  python -m eval.run_eval check\pred_full_v12 > check\eval_v12.json
  Compress-Archive check\pred_y_rep1, check\pred_y_rep2, check\pred_y_rep3, check\pred_full_v12, check\eval_v12.json check\specy.zip -Force
  ```
  - 開頭「程式版本」應為最新 commit 且無 `+dirty`;C073 首次執行會有 3 次 DI 呼叫(新 PDF)

- [x] 4.22 SPEC-ah 監控告警 + 事件觸發(2026-10-07 程式側 ✓;**2026-10-08 ⑧ 重建時真實觸發驗證:T1 事件 Job Succeeded、T2 壞請求 → 告警信 6 分鐘到**,見 EVIDENCE 44):
  ```powershell
  git pull origin development; if ($LASTEXITCODE) { throw "pull 失敗" }
  $sha = (git rev-parse --short=7 HEAD)
  # 先 what-if(假設 2:KEDA 規則的 identity 欄位 ARM 是否接受)
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters enableJob=true enableReview=true reviewEmailTo=<信箱> enableMonitoring=true enableEventTrigger=true imageTag=$sha
  az deployment sub create  --location eastus --template-file infra\bicep\main.bicep --parameters enableJob=true enableReview=true reviewEmailTo=<信箱> enableMonitoring=true enableEventTrigger=true imageTag=$sha
  $st = (az deployment sub show -n main --query properties.outputs.storageAccountName.value -o tsv)
  # T1 正常請求:1 案(≈NT$1)
  '{"case_ids":["C003"]}' | Out-File -Encoding ascii check\req_ok.json
  az storage blob upload --account-name $st -c claims-inbox -n "requests/ok-$(Get-Date -f yyyyMMddHHmm).json" -f check\req_ok.json --auth-mode login
  # 約 1~2 分鐘內應出現一次執行;完成後 runs/<run_id>/run_meta.json 的 request 欄為上傳路徑
  az containerapp job execution list -n caj-claimcrew-event -g rg-claimcrew -o table
  # T2 壞請求(真實觸發告警,固定解法 7):不跑管線、exit 2,15~30 分鐘內應收到告警信
  '{"case_ids":["--all"]}' | Out-File -Encoding ascii check\req_bad.json
  az storage blob upload --account-name $st -c claims-inbox -n "requests/bad-$(Get-Date -f yyyyMMddHHmm).json" -f check\req_bad.json --auth-mode login
  ```
  - 失敗時第一動作:`ContainerAppSystemLogs_CL` 查 `KEDAScalerFailed`(pre-mortem:佇列 Reader 角色)、Event Grid 訂閱的 delivery metrics、佇列是否有殘留訊息
- [x] 4.23 改善流程 ⑧:拆除 → 重建一次(**2026-10-08 完成**,見 EVIDENCE 44)(驗證「平時 $0、需要時從 repo 重建」+ 一併真實觸發 4.22;預估 ≈NT$10–50,另有 ACR Basic 每天約 NT$5)
  **每一步記開始/結束時間**(E1 要的是「重建要多久」),寫在 `check\rebuild_log.txt`。資源名稱由 `uniqueString(資源群組 id)` 決定,同訂閱同名 `rg-claimcrew` 重建後 storage / ACR 名稱**不變**。
  ```powershell
  # ── 0. 拆前(全部免費)──
  git pull origin development; if ($LASTEXITCODE) { throw "pull 失敗" }
  az bicep build --file infra\bicep\main.bicep --stdout | Out-Null          # 0 error 0 warning 才往下
  az resource list -g rg-claimcrew --query "[].{name:name,type:type,loc:location}" -o table | Out-File -Encoding utf8 check\pre_teardown_resources.txt
  $st = az storage account list -g rg-claimcrew --query "[0].name" -o tsv     # Bicep 依訂閱自動命名的 storage 帳戶
  $env:CLAIMCREW_STORAGE_ACCOUNT = $st
  python tools\sync_cache.py pull check\di_cache                              # DI 快取以雲端為準補齊本機(拆後唯一來源是本機)
  (Get-ChildItem check\di_cache -Recurse -File).Count                         # 記下檔數,重建後 push 要對得上
  New-Item -ItemType Directory -Force check\cloud_runs | Out-Null
  az storage blob download-batch --account-name $st -s runs --auth-mode login -d check\cloud_runs   # 歷次雲端輸出(拆後消失)

  # ── 1. 拆除 ──(不可逆:DI 模型 diagnosis-cert-v3、Search 索引、ACR 映像、Blob 全部消失;皆可由 repo 重生)
  az group delete -n rg-claimcrew --yes                                      # 不加 --no-wait,順便計時
  az cognitiveservices account list-deleted -o table                         # Cognitive Services 有 48 小時軟刪除
  # 逐一 purge(同名重建前必做,否則部署回「name already in use」):oai / fdy 在各自區域,其餘 eastus
  az cognitiveservices account purge -l eastus2 -g rg-claimcrew -n oai-claimcrew
  az cognitiveservices account purge -l eastus  -g rg-claimcrew -n di-claimcrew
  az cognitiveservices account purge -l eastus  -g rg-claimcrew -n fdy-claimcrew
  az cognitiveservices account purge -l eastus  -g rg-claimcrew -n cs-claimcrew
  #   list-deleted 裡若還有 eastus2 的 fdy-claimcrew(claimcrew-probe 殘留)一併 purge:-l eastus2
  az cognitiveservices account list-deleted -o table                         # 必須為空
  # 隔天在 Cost analysis 確認 rg-claimcrew 費用歸零(「平時 $0」的證據截圖)

  # ── 2. 重建基礎(imageTag 留空 → 不建 Job;W16)──
  $sha  = (git rev-parse --short=7 HEAD)
  $mail = "你的覆核信箱@outlook.com"                                          # 禁止寫 <信箱>(PowerShell 重導向)
  $p = "baseName=claimcrew enableFoundry=true enableReview=true reviewEmailTo=$mail enableJob=true enableMonitoring=true enableEventTrigger=true"
  az deployment sub what-if --location eastus --template-file infra\bicep\main.bicep --parameters $p.Split(' ')
  az deployment sub create  --location eastus --template-file infra\bicep\main.bicep --parameters $p.Split(' ')
  az resource list -g rg-claimcrew --query "[].{name:name,type:type,loc:location}" -o table   # 應無 cos-claimcrew、無 log-claimcrew-job,多 log/appi/ag/alert、evgt 訂閱

  # ── 3. 個人帳號資料面角色(隨資源刪除而消失)──
  $me = az ad signed-in-user show --query id -o tsv
  $scope = { param($n,$t) az resource show -g rg-claimcrew -n $n --resource-type $t --query id -o tsv }
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Storage Blob Data Contributor" --scope (& $scope $st Microsoft.Storage/storageAccounts)
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Cognitive Services User"        --scope (& $scope di-claimcrew Microsoft.CognitiveServices/accounts)
  az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Cognitive Services OpenAI User" --scope (& $scope oai-claimcrew Microsoft.CognitiveServices/accounts)
  #   Foundry User 依 4.14 對專案範圍授予(只在要跑 Repair / 探針時需要)

  # ── 4. Outlook 連線授權(Bicep 做不到)──
  #   Portal → rg-claimcrew → outlook-claimcrew → General → Edit API connection → Authorize → Save(W14:漏按 Save 不生效)

  # ── 5. DI custom neural 重訓(同 ID diagnosis-cert-v3,程式不用改)──
  python -m app.datagen.di_labels --assemble check\di_upload
  az storage container create -n di-training --account-name $st --auth-mode login
  az storage blob upload-batch -d di-training -s check\di_upload --account-name $st --auth-mode login --overwrite   # 預期 143
  #   Studio:新建專案(新 DI 資源)→ Run layout on all documents(F0 429 屬預期,重跑 unanalyzed 至 71 全綠)→ Train Neural,ID 填 diagnosis-cert-v3
  python tools\di_smoke.py C008 --entra                                       # 8/8 才往下;C055 再跑一次

  # ── 6. Search 索引重建 ──
  $env:SEARCH_KEY = (az search admin-key show --service-name srch-claimcrew -g rg-claimcrew --query primaryKey -o tsv)
  $env:AOAI_KEY   = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
  python tools\search_index.py create
  python tools\search_index.py selftest -k 10 --core                          # 對照 4.12:71/71

  # ── 7. 映像 + Job + DI 快取 ──
  az acr build -r ("acrclaimcrew5plo5lmu3rgxc") -t "claimcrew:$sha" --build-arg GIT_SHA=$sha --no-logs .
  az deployment sub create --location eastus --template-file infra\bicep\main.bicep --parameters ($p + " imageTag=$sha").Split(' ') --query "properties.parameters.reviewEmailTo.value" -o tsv   # 必須印出信箱(W25)
  az containerapp job show -n caj-claimcrew-pipeline -g rg-claimcrew --query properties.template.containers[0].image -o tsv   # 尾巴必須是 $sha
  az containerapp job show -n caj-claimcrew-event    -g rg-claimcrew --query properties.template.containers[0].image -o tsv
  python tools\sync_cache.py push check\di_cache                              # 上傳數 = 步驟 0 記下的檔數
  #   等 5 分鐘(RBAC 傳播)

  # ── 8. 真實觸發(接 4.22 的 T1 正常請求 / T2 壞請求告警)──
  #   T1:C003 一案,run_meta status=succeeded、request 欄正確;判定/金額與歷次 C003 比對(DI 走快取、0 次新 DI 呼叫)
  #   T1 完成後(workspace 已有 ContainerApp*_CL 表)才建告警規則——新 workspace 直接建會回
  #   「Operator source expression should be table or column」,skipQueryValidation 無效(2026-10-08 實測兩次)
  az deployment sub create --location eastus --template-file infra\bicep\main.bicep --parameters ($p + " imageTag=$sha enableJobAlert=true").Split(' ')
  #   T2:壞請求 exit 2,15~30 分鐘內收到告警信 → 截圖 docs\screenshots\20261008_job_failed_alert.png
  ```
  - 驗收(事前提交):①步驟 1 隔天 rg 費用 0 ②重建 what-if/create 無錯誤、資源清單符合預期 ③di_smoke C008/C055 8/8 ④selftest 71/71 ⑤T1 成功且 C003 判定/金額與歷次相同 ⑥T2 告警信收到 ⑦總重建時間(步驟 2~7)記錄於 build log
  - 任一步失敗:先貼完整錯誤給雲端 session,**不要**在 Portal 手動補資源(脫離 Bicep 真相源)
  - F0 DI 每月 500 頁:步驟 5 Run layout 會用掉一部分(約 71 份 × 頁數),本月若已接近上限,步驟 5 改到下個月或先查用量

## 7. 長期停放後重建:預期漂移清單(2026-10-16 首版)

> 用途:拆除後停放數月~一年再重建時,**先跑 7.1 預檢**,逐項比對 7.2,再照 4.23 重建。
> 標記:**[實證]** = 本專案已實際踩過(附 W 編號 / EVIDENCE);**[預期]** = 依版本釘選與服務政策推估、尚未發生。
> 實測耗時:拆除 27:08(10/08)、15:09(10/16);重建 10/08 部署 4 次才成功(含 eastus 容量不足等待 70 分),**總時長未計時——下次重建請從 7.1 開始記錄每步起訖時間**。

### 7.1 重建前預檢(全部唯讀、$0)

```powershell
. .\check\env.ps1                                                       # 載入 $tenant
az login --tenant $tenant                                               # MFA(W6)
az account show --query "{name:name,state:state}" -o table              # 訂閱仍為 Enabled
az bicep version; az bicep build --file infra\bicep\main.bicep --stdout | Out-Null   # 0 error 才往下(W5)
az cognitiveservices model list -l eastus2 --query "[?model.name=='gpt-4o' && model.version=='2024-11-20'].model.{v:version,status:lifecycleStatus,retire:deprecation.inference}" -o table
az cognitiveservices model list -l eastus2 --query "[?model.name=='gpt-4.1-mini' && model.version=='2025-04-14'].model.{v:version,status:lifecycleStatus,retire:deprecation.inference}" -o table
az cognitiveservices usage list -l eastus2 -o table                     # Standard gpt-4o / gpt-4.1-mini 剩餘 TPM 配額
az cognitiveservices account list-deleted -o table                      # 必須為空,否則先 purge(4.23 步驟 1)
az search service list --query "[?sku.name=='free'].name" -o tsv        # 每訂閱僅 1 個 free Search;有別的就會撞
az cognitiveservices account list --query "[?sku.name=='F0' && kind=='FormRecognizer'].name" -o tsv   # DI F0 名額
```
> PowerShell 下 `--query "[?...]"` 若報 `was unexpected at this time`,改用 `| ConvertFrom-Json` 過濾(W24)。

### 7.2 漂移項目

| # | 項目(目前釘選) | 類型 | 最早徵兆 | 偵測 | 處置 |
|---|---|---|---|---|---|
| D1 | 主模型 gpt-4o **2024-11-20** / Standard(`openai.bicep`、`foundry.bicep`) | [實證] 舊版曾 Deprecating(W7);本版 Bicep 註記退役 2027-04-14 | what-if 回 `ServiceModelDeprecating` | 7.1 model list 的 lifecycleStatus | 改 `version` 參數 → **全量回歸**(固定解法 29 區間判準;LLM 規範第 2 條:禁止假設行為不變)|
| D2 | 抽取模型 gpt-4.1-mini **2025-04-14** | [預期] 同 D1 機制 | 同 D1 | 同 D1 | 同 D1;ADR-007 的路由前提需重量 |
| D3 | Embedding text-embedding-3-small **v1** | [預期] | 同 D1 | 同 D1 | 改版本後重建索引 + `search_index.py selftest` |
| D4 | OpenAI 配額:Standard 50K TPM、GlobalStandard 0(eastus2) | [實證] 首次部署即撞(SPEC §14) | `InsufficientQuota` | 7.1 usage list | 申請配額或降 `capacity`;全量 73 案約 25 分鐘以 50K 為前提 |
| D5 | Container Apps 區域容量(`containerAppsLocation=eastus2`) | [實證] W23 | `AKSCapacityHeavyUsage` / `ManagedEnvironmentCapacityHeavyUsageError` | 部署失敗訊息 | 間隔重試或換區域(只動環境與 Job) |
| D6 | 免費層:AI Search **free**、DI **F0**(每訂閱名額有限) | [預期] | `ServiceQuotaExceeded` / 名稱已存在 | 7.1 最後兩行 | 刪除其他免費資源,或改付費 SKU(預算評估) |
| D7 | 軟刪除 48 小時(DI / Foundry / OpenAI) | [實證] 4.23 | 同名重建 `name already in use` | 7.1 list-deleted | `az cognitiveservices account purge` |
| D8 | ARM API 版本,**含 preview:`scheduledQueryRules@2023-03-15-preview`** | [預期] preview 版最可能先下架 | what-if 報 `NoRegisteredProviderFound` / `InvalidApiVersion` | `az bicep build` + what-if | 換成當時 GA 版本,重跑 `bicep_check` |
| D9 | 資料面 API:DI `2024-11-30`(`di_client.py`、`di_smoke.py`)、AOAI `2024-10-21`(`aoai_client.py`) | [預期] | 呼叫回 404 / `UnsupportedApiVersion` | `di_smoke.py C008 --entra` 冒煙 | 改常數 → 單元測試 + 冒煙 + 子集 eval |
| D10 | 告警規則需 Log Analytics 表已存在 | [實證] W22 | `Operator source expression should be table or column` | 部署失敗訊息 | 兩段部署:先建,首跑 Job 後再開 `enableJobAlert` |
| D11 | Outlook.com 連線授權與寄送限制 | [實證] 4.17 步驟 3;逐案寄送第 6 封被封(W15) | 摘要信不寄達、Logic App 401 | Logic App run history | Portal 重新 Authorize + **Save**(W14);維持一輪一封摘要 |
| D12 | DI 自訂模型無法匯出,需重訓 `diagnosis-cert-v3` | [實證] ⑧ | `di_smoke` 404 ModelNotFound | `di_smoke.py C008 --entra` | Studio 重訓(4.23 步驟 5);**重訓後信心值會變**(⑧:C055 issue_date 0.67→0.94),先 push DI 快取再跑可保持與歷次一致 |
| D13 | DI 快取(本機 303 檔,本檔第 8 節) | [實證] | 全量時大量新 DI 呼叫、分數漂移 | 跑案後 `_run.jsonl` 的快取命中數 | 重建後先 `sync_cache.py push check\di_cache` |
| D14 | Bicep CLI(CI 釘 0.36.1)vs 桌機 `az bicep` 新版 | [實證] W5 | `BCP318` 等新警告 | `az bicep version` | 以 CI 版本為準;新警告只修型別,不改語意 |
| D15 | 容器基底 `python:3.11.9-slim-bookworm`、`requirements*.txt` 釘版 | [預期] Python 3.11 生命週期、套件漏洞 | `az acr build` 失敗或 `pip-audit` 新漏洞 | `bash tools/gauntlet.sh`(pip-audit 層) | 升版走一般 SPEC 流程,不在重建當下順手升 |
| D16 | 角色名稱變更(例:Azure AI User → Foundry User) | [實證] SPEC §14 | Portal 找不到舊角色名 | — | Bicep 用角色 GUID 不受影響;手動指派時依 GUID 查 |
| D17 | 價目(`tools/cost_report.py` 釘選 eastus2 價格) | [預期] | 成本報表與帳單對不上 | 對照 Cost analysis | 更新價目表並註記日期 |

### 7.3 重建後驗收(最小集合)
1. `az resource list -g rg-claimcrew -o table` 與 `check\pre_teardown_resources_1012.txt`(21 項)比對,差異逐項說明
2. `di_smoke.py C008 --entra` 8/8;`search_index.py selftest -k 10 --core`
3. 上傳 1 案請求 → 事件 Job Succeeded → 摘要信寄達(4.22 T1)
4. 子集 13 案跑一輪,放行錯 0(固定解法 35)後,才跑全量

---

## 8. 拆除前備份清單與還原位置(2026-10-16)

> 拆除後,下列本機檔案是**唯一來源**;還原時照「還原指令」執行。雲端上可由 repo 重建者(DI 訓練資料與模型、Search 索引、ACR 映像、Logic Apps、Job)不在此清單,依 runbook 4.23 重建。

| 項目 | 本機位置(桌機 `C:\ClaimCrew`) | 數量 | 用途 | 還原指令 |
|---|---|---|---|---|
| 資源清單 | `check\pre_teardown_resources_1012.txt` | 23 行 = 表頭 2 行 + **21 項資源** | 重建後比對 | 不需還原;重建後執行同一 `az resource list` 比對 |
| DI 辨識快取 | `check\di_cache` | **303 檔**(其中 225 檔為雲端 `di-cache` 容器內容;另 78 檔為僅存本機的快取) | 重建後免重付 DI、辨識結果與歷次一致 | `python tools\sync_cache.py push check\di_cache`(只上傳雲端沒有的檔,不覆蓋) |
| 雲端輸出 + 覆核紀錄 | `check\cloud_runs`(`runs` 容器整份:`runs/<run_id>/…`、`reviews-inbox/…`) | **841 檔**(含 `runs/` 歷次輸出、`reviews-inbox/` 5 筆覆核、`noisy/afe6c76/` SPEC-ak 雜訊 PDF 78 檔) | 歷次 run 證據、eval 重算、覆核統計 | 一般不需還原(離線 eval / regate 直接讀本機);若要放回:`az storage blob upload-batch -d runs -s check\cloud_runs --account-name <新 storage> --auth-mode login` |
| 異地備份 zip | `check\cloud_runs_final_1012.zip`、`check\di_cache_final_1012.zip` | `cloud_runs_final_1012.zip` 24,608,566 bytes(約 23.5 MB);`di_cache_final_1012.zip` 257,157 bytes(約 251 KB) | 本機遺失時的最後備份 | 解壓回上列位置 |
| 異地備份存放處 | **異地雲端備份**(2026-10-16 複製兩個 zip) | 2 檔 | 本機 `check\` 遺失時的來源 | 從異地雲端備份下載兩個 zip → 解壓回 `C:\ClaimCrew\check\` |

**注意**:`check\` 已 gitignore,這些檔案**不會進 repo**;本表只記錄位置與數量。
