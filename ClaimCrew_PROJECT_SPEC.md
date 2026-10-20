# ClaimCrew — Azure 全託管醫療保險理賠多代理審核系統

> **本檔用途**:專案唯一真相源(single source of truth)。任何環境(雲端對話 / Claude Code / 手動開發)的設計討論結論,定案後**當場回寫本檔**「已定案決策」區。
> **配套**:內部開發規則(未公開;公司範本改個人版)只管「怎麼做事」;本檔管「做什麼、為什麼、做到哪」。
> **命名備註**:專案名 **ClaimCrew** 定案(2026-09-01),repo 名與本檔檔名皆已一致。

---

## 1. 專案定位(一句話)

保險公司收到理賠申請(診斷證明書 + 醫療收據 + 申請書)後,由**多個 AI agent 協作**完成文件抽取、保單條款比對(帶引用)、初審判定與人工覆核路由 — **全程 Azure 託管服務、零自建基礎設施**,並以 build log 實證「從零到可用 N 天」的雲端快速交付能力。

**核心命題**:本專案賣的不是模型準確率,是 **Azure 託管服務帶來的開發速度與整合效率** — 對照作者公司 on-prem 專案的月級交付週期,量化呈現雲端全棧的價值主張。

**目標讀者**(依序):Microsoft / 雲廠商 SA 面試官、海外 FDE 面試官、企業 LLM 交付商(跳板公司)技術主管。

## 2. 背景與缺口分析(2026-08 定案)

作品集現況:4 公司專案(MRRA/SOAP/MAS/GDC,on-prem,受保密限制)+ 5 個人專案(MedAgent-EHR / SelectiveCXR / NDD / BP / SelectRAG 暫停中)。

九個專案共同缺口(本專案一次補齊):
1. **多代理協作** — AI-103 旗艦考點,九專案無一涉及(MedAgent-EHR 為單 agent ReAct)
2. **雲原生託管服務整合** — 現有專案全數 on-prem 或單一雲服務點狀使用
3. **IaC 一鍵部署** — 從未展示過 Bicep / 基礎設施即程式碼
4. **「快速交付」敘事** — 公司專案皆為數月週期,無法佐證雲端提速主張
5. **成本工程** — 從未公開展示過雲端成本設計與監控

**與 AI-103 的關係**:認證剛到手,本專案是其實作驗證 — Foundry Agent Service、Document Intelligence、AI Search、Content Safety、Logic Apps 全數落地,讓證照從「訊號」變「能力」。

## 3. 不重複驗證(與既有九專案逐一對照)

| 既有專案 | 差異定位 |
|---|---|
| GDC(公司,產生診斷證明書) | **產業鏈對岸**:GDC 讓文件出生(醫院端),本專案讓文件核銷(保險端)。面試敘事:「我做過這份文件的出生與死亡,理解整條產業鏈資料流」 |
| MRRA(公司,病歷偵錯) | 院內單文件不一致偵測 vs 跨文件、跨機構理賠審核;仲裁思想可傳承(政策查表、旗色分流),場景全新 |
| MAS(公司,醫療摘要) | 摘要生成 vs 文件工作流;「有據不假」引用查核紀律移植到條款比對 |
| SOAP(公司,公告監控) | 爬蟲+標準化 vs 多代理審核;無交集 |
| MedAgent-EHR(個人) | 單 agent ReAct + FHIR + 本地自架 vs **多 agent 協作** + 文件工作流 + **全雲託管**;Verifier 安全閘思想傳承 |
| SelectiveCXR(個人) | 影像+不確定性 vs 文件+工作流;「低信心轉人工」的分流哲學傳承 |
| SelectRAG(個人,暫停) | 選型實驗(本地 NVIDIA 棧)vs 整合交付(雲託管);RAG 在本專案只是一個元件而非主題。兩案並列 =「實驗室 + 交付現場」互補敘事 |
| NDD / BP(個人,早期) | 無交集 |

## 4. 資料計畫(全合成,零 PHI)

- **保單條款庫**:自行撰寫 2–3 份虛構醫療險保單(住院醫療、手術、實支實付各一),每份 30–80 條條款,涵蓋:給付項目、除外責任、等待期、既往症、限額規則。格式 PDF(排版仿真)
- **理賠申請件**:合成 40–60 案,每案含:①診斷證明書(仿 GDC 輸出格式 — 敘事連續性彩蛋)②醫療收據(項目化費用)③理賠申請書。以 LLM 輔助生成 + 人工校訂
- **真值標註**:每案人工判定「應核付 / 應拒付(附條款依據)/ 需人工(資訊不足)」三分類 + 核付金額 — 凍結為 eval set(MRRA 紀律:真值人工判定、凍結後改動需記錄)
- **案件設計刻意涵蓋**:順利件、除外責任件、等待期未滿件、文件缺漏件、收據與診斷不符件(MRRA 偵錯思想)、金額超限件
- **紅線**:任何真實病歷 / PHI / 公司內部文件格式**絕不進入**本專案;合成資料生成 prompt 遵守中性占位符原則後再具象化

## 5. 系統架構

### 5.1 資料流

```
申請文件上傳(Blob Storage)
   → 事件觸發(Event Grid / Logic Apps)
   → Document Intelligence
       ├─ prebuilt-receipt:醫療收據
       ├─ prebuilt-read/layout:申請書
       └─ custom neural:診斷證明書欄位(訓練 5+ 份合成樣本)
   → Foundry Agent Service 多代理協作(見 5.2)
   → 判定產出(結構化 JSON:決定/金額/條款引用/信心分數)
   → Content Safety + groundedness 檢核(判定主張必須綁條款出處)
   → 分流:
       ├─ 高信心 → 自動出函(合成通知書)
       └─ 低信心/爭議 → Logic Apps 人工覆核流程(email / Teams 卡片)
   → Blob `runs/<run_id>/` 全案落盤(各案判定、Gate、耗時、token 成本 + run_meta;原設計 Cosmos DB,2026-10-07 量測零使用後移除,ADR-010)
   → Application Insights 監控儀表(延遲、成本/案、agent 呼叫鏈)
```

### 5.2 Agent 編制(Foundry Agent Service)

| Agent | 職責 | 關鍵設計 |
|---|---|---|
| **Intake Agent** | 文件完整性檢查(三件是否齊、格式是否可讀) | 缺件即中止並產出補件清單,不進入後續流程(省 token) |
| **Extraction Agent** | 整合 Document Intelligence 產出,結構化為統一 claim schema | 欄位信心分數傳遞;低信心欄位標記 |
| **Policy Agent** | 以 AI Search 檢索適用條款(hybrid search),產出「條款依據清單」 | **每條依據必附條款編號與原文引用**(MAS「有據不假」紀律);檢索不到依據 = 明確回報,禁止腦補 |
| **Adjudication Agent** | 綜合前三者產出初審判定 + 核付金額 + 信心分數 | 判定理由逐條綁定 Policy Agent 的引用;三分類(核付/拒付/人工) |
| (程式側)**Gate** | 結構化檢核:引用條款是否真實存在、金額計算複核、信心門檻分流 | MedAgent Verifier 思想:LLM 判定 + 程式側閘門雙層防護;旗色/分流邏輯**政策入程式**(MRRA 教訓:LLM 只判事實,分流由查表決定) |

### 5.3 模型分層路由(成本工程)

- Intake / Extraction 整理類任務 → **GPT-4o-mini 級**(便宜 10–20 倍)
- Policy 比對與 Adjudication → **GPT-4o 級**
- 路由決策寫進架構決策備忘錄(含 token 成本實測對照)— 這是 SA 面試的標準考題「怎麼控制 LLM 應用成本」的實證答案

## 6. 評測框架

1. **判定準確率**:對凍結 eval set(40–60 案)的三分類 accuracy + 核付金額誤差;混淆矩陣重點看「應拒付被核付」(最傷)與「應核付被拒付」
2. **引用正確率**:每筆判定依據反查條款庫 — 條款編號存在?原文一致?適用性人工抽核(MAS 查核紀律雲端版)
3. **分流品質**:低信心轉人工的案件中,真實疑難案佔比(轉人工要轉得有道理,呼應 SelectiveCXR 的 selective prediction 哲學)
4. **效能與成本**:每案端到端延遲、token 成本、Azure 月成本 — App Insights 實測,呈現於專案頁
5. **一行指令重跑**:eval 腳本一鍵跑全案,改 prompt / 路由前後必跑,退步撤回(MRRA 基準鐵律)

## 7. 「快速開發與整合」的實證方法(本專案的論文命題)

1. **Build Log(公開)**:逐日記錄開發時數、完成里程碑、卡點與解法;最終產出「Solo builder, N days from zero to working claims pipeline」— 與公司 on-prem 月級週期的對照即 Azure 價值主張實證
2. **整合成本記帳**:每個服務接入耗時(DI 接入 X 小時、Agent Service Y 小時…),對照「若自建同等能力」的估算 — SA 對客戶講「buy vs build」的現成論據
3. **架構決策備忘錄(ADR)**:每個服務選型的理由 + 被否決替代方案(如:為何 Foundry Agent Service 而非自架 LangGraph;為何 Logic Apps 而非 Functions;Search Free vs Basic 的取捨)— SA 交付物格式
4. **成本儀表公開**:月帳單分解 + 每案成本,含省錢決策說明(見 §9)

## 8. Azure 服務對應與層級決策

| 服務 | 用途 | 層級決策(含成本理由) |
|---|---|---|
| Blob Storage | 文件收件匣 | Standard LRS,趨零成本 |
| Document Intelligence | 三類文件抽取 | **F0 起步**(500 頁/月免費,demo 量夠);custom neural 訓練配額另查定價頁 |
| Azure AI Search | 條款檢索(hybrid) | **Free tier 起步**(3 indexes/50MB,合成條款庫僅數 MB)。註:Azure OpenAI On Your Data 需 Basic+,但本專案由 agent 手動呼叫 Search API,Free 可用 — 此選型判斷寫入 ADR。面試高峰期可升 Basic($73.73/月)取得 SLA 與語意排序;Serverless preview(2026-09-13 前免費)為備選 |
| Foundry Agent Service | 多代理協調 | 計費隨底層模型 token;分層路由壓成本(§5.3) |
| Azure OpenAI | 生成與判定 | GPT-4o + mini 分層;所有呼叫經 App Insights 記 token |
| Content Safety | 輸入過濾 + groundedness | F0 起步 |
| Logic Apps | 人工覆核工作流(email/Teams) | Consumption,趨零成本 |
| ~~Cosmos DB~~ | 已移除(2026-10-07,ADR-010:程式零引用、10 月帳單 <NT$0.01;落盤改由 Blob `runs/`) | — |
| Container Apps | 批次管線 **Job**(手動觸發 72 案 + 一封摘要;SPEC-t 以「真實使用型態是批次」改掉原 API + 審核 UI 設定) | Consumption Job,scale-to-zero、只在執行時計費 |
| Application Insights | 監控與成本儀表 | 5GB/月免費額度內 |
| **Bicep** | 全套 IaC | **一鍵部署 / 一鍵拆除**;非展示期整組拆掉,面試前 15 分鐘重建 — 拆建紀律即成本控制手段,本身就是 SA 敘事 |

## 9. 成本預算(2026-08 估算,USD 列表價)

- **情境 A(全程常駐,Search Basic)**:總計約 $220–280 / 10 週(Search $166 為最大單項)
- **情境 B(精實:Search Free + DI F0 + Bicep 拆建)**:總計約 $40–90 / 10 週
- **採用策略**:預設情境 B,面試高峰期臨時升級;預算上限 **NT$5,000**,超標即檢討架構
- 新 Azure 帳號可用 $200 首月抵用金(若適用)

## 10. 交付物

| 交付物 | 說明 | 變現對象 |
|---|---|---|
| GitHub repo(public,個人帳號) | 程式 + Bicep + 合成資料生成器 + eval 腳本(一鍵重跑) | 履歷連結 |
| GitHub Pages 專案頁(中英雙語) | 沿用 MedAgent/SelectiveCXR 版式;含架構圖、build log、成本儀表截圖 | 作品集 |
| **架構決策備忘錄(ADR,英文)** | 服務選型 + 被否決方案 + 成本工程 | SA 面試現成 case study |
| **Build Log** | 「N 天從零到可用」逐日紀錄 | 快速交付敘事的證據 |
| Demo 影片(英文旁白) | 端到端走一案:上傳 → 多代理協作 → 判定 → 人工覆核路由 | 英文口說練習 + 作品展示一石二鳥 |
| **重建 Runbook + 預期漂移清單** | 拆除後長期停放(數月~一年)再重建的操作手冊。**前提原則:repo 保存的是「可重生一切的原料與腳本」,非產物本身**(DI custom neural 模型不可匯出,保存訓練樣本+標註檔重訓;Search 保存條款 PDF+建索引腳本,非索引)。內容:①內容物重灌腳本 ②漂移檢查清單(模型版本退役、Agent SDK breaking change、free tier 條款、OpenAI 配額重申請、soft delete purge)③實測重建耗時紀錄。**Runbook 定位為「上次重建成功的步驟+版本釘選」,每次重建撞到的坑回寫**(非即時維護);時程預期=一鍵部署+半天~兩天修漂移 | 成本工程敘事核心:「平時 $0、面試前重建」;SA 面試「怎麼控制雲端成本」的實證答案 |
| 英文技術文章 1–2 篇 | 「Building a multi-agent claims processor on Azure in N days」+「Cost engineering a demo-grade AI system」 | 英文計畫載具 |

## 11. 階段規劃(合計 6–8 週業餘;高強度可壓 1 週建置 + 迭代)

### Phase 0:零成本前置(建任何 Azure 資源之前,一次做齊)

> **原則:壓縮計費窗口(compress the metered window)**——所有不需雲的工作先完成,Azure 計費時鐘最後啟動;付費期只做「只有雲上能做的事」。此順序本身為成本工程手法,寫入 ADR。

**狀態:2026-09-01 全數完成,Azure 支出 $0。**

- [x] 合成資料全量:3 份保單(POL-A/B/C,共 100 條,MD+PDF)、72 案三件套(JSON+仿真 PDF)、真值經人工檢核後凍結;DI custom neural 訓練標註 71 份(自動座標;PDF 不重複入庫,`--assemble` 組裝)
- [x] 統一 claim schema 定案(`app/schemas/claim.py`,Pydantic v2 單一真相源;structured outputs 子集轉換層)
- [x] 程式側 Gate(`app/gate/`:條款索引反查/引文一致/金額上界/信心分流)+ eval 腳本(`eval/run_eval.py`,`--selfcheck` 對 40 案滿分)
- [x] 四 agent prompt 全套草稿(`app/agents/prompts/`)
- [x] Bicep 模板涵蓋 §8 全部服務(`infra/bicep/`,10 模組,`bicep build` 0 errors;Content Safety/Logic Apps/App Insights/Container Apps/Event Grid 以 `enablePhase2` 隔離,Phase 1 預設不建;GPT-4o/4o-mini 版本釘選)
- [x] CI(GitHub Actions,gauntlet+selfcheck+bicep)+ build log + 英文 ADR ×7 + 專案頁中英雙檔

**需等 Azure 才能做**:DI 實訓練與抽取、Search 真索引、OpenAI 呼叫、Agent Service 編排、端到端整合。
**Phase 0 留下的兩個待驗證掛號——均已於 Phase 1 結案**:①structured outputs schema 的真 API 相容性(2026-10-01 煙霧測試通過,八輪 72 案全量零 schema 拒絕)②DI labels/fields 格式(首訓原樣接受;另修「同值共框」「跨行截斷」兩個標註缺陷後 v3 真實觸發 8/8)。

### Phase 1:端到端骨架(第 1–2 週;**計費窗口自此開啟**)

> 原規劃的「合成資料第一批」「eval 腳本 v1」已由 Phase 0 提前完成,故本階段只剩須連雲的工作。

- [x] Bicep 首次部署(2026-09-30,`az deployment sub create` 11 資源 4 分 23 秒;釘選 gpt-4o 2024-08-06 / 4o-mini 07-18 遭 `ServiceModelDeprecating` 拒絕 → 改 gpt-4o 2024-11-20 / Standard 單模型,mini 以旗標關閉;OpenAI 帳戶因配額申請移至 eastus2)
- [x] DI 接入:診斷書 custom neural `diagnosis-cert-v3`(F0 可 neural、0.5 訓練小時;掛號 ② 過關,另修同值共框、跨行截斷;C008/C055 真實觸發 8/8,信心 0.67~0.98);收據與申請書走 `prebuilt-layout` 版面文字由抽取呼叫整理為結構(方案 b),八輪全量驗證;DI 磁碟快取(sha256 × 模型)使 F0 月額度耗盡後仍可零用量重跑
- [x] 條款入 Search(2026-10-01:索引 104 文件、hybrid recall@10 0.835;A/B 72 案 core+search 僅省 6.2% tokens 且 Gate 前 59 vs 61 → `full` 全文注入為預設,ADR-005 補記)
- [x] **單 agent 版**端到端跑通(2026-10-01 `tools/run_pipeline.py`:DI → 抽取呼叫 → 條款上下文 → 判定呼叫 → 程式加總 → Gate;gpt-4o 50K TPM,72 案約 15 分鐘(快取後)/ 約 $3.5;掛號 ① 同時驗證)
- [x] 真實 eval 八輪(2026-10-01~02):首跑 Gate 後 0.139(門檻 0.80 誤打誤撞替模型擋錯)→ SPEC-b~j 十輪 Gate 強化 → 終版 Gate 對七輪真實輸出 504 案次**放行 161 件金額全對、危險錯 0**;v8 終版 Gate 前 0.792 / 後 0.542(「寧可人工」代價;run-to-run ±3 案);EVIDENCE 十二部分見 `docs/evidence/`

### Phase 2:多代理化與防護(第 3–5 週)
- [x] 多代理化(2026-10-03 **ADR-008 accepted**:按驗證邊界拆分;Repair agent 經 R1/R2 兩輪 72 案重放否決放行權(每件放行錯對應一個 Gate 洞),降為 **顧問模式** 承載於 Foundry Agent Service(Agents v2、Gate 為 function tool、≤2 輪);方案 P 以確定性手段取得增益——deny 類旗標改派 deny(八組 40/40 真值 deny)、分段逐日限額檢核;Foundry 帳戶搬 eastus(gpt-4o 11-20 + 4.1-mini)):**按驗證邊界拆分,角色對應量測缺口**(Policy 獨立 agent 經 SPEC-m 探針否決:10 案 recall 0.772 < 0.80、第11條輔助金 ×4 漏引;Phase 2 候選角色 Repair = 以 Gate 為工具的有界迴圈 agent,承接 Gate 前/後落差 0.819→0.694);**Foundry Agent Service 承接角色內工具迴圈,角色間路由留程式**(ADR-006);Phase 1 單 agent 為基準,拆分須以 eval 證明不退步 + 程式側 Gate(Phase 1 已 32 種旗標,Phase 2 候選:收據類別→明細行覆蓋、manual→deny 方向)。**勘誤 2026-10-02**:本行原寫「直呼 API 編排,非 Agent Service——ADR-005 已否決」為誤寫,ADR-005 否決的是 AI Search serverless preview,Agent Service 從未被否決(ADR-001 明列其為編排層)
- [x] 模型分層路由 + token 成本記帳(2026-10-02 ADR-007 accepted:抽取 gpt-4.1-mini / 判定 gpt-4o,72 案 eval 不退、每案 0.0456 → 0.0302 USD(−33.8%);逐呼叫記帳 `calls` 含 cached、`tools/cost_report.py` 價目表自 Retail Prices API 釘入;原前提「抽取為大頭」推翻——抽取僅佔 prompt 39%)
- [x] Content Safety + groundedness 接入(SPEC-v 2026-10-05:F0 + Entra + Bicep `enableContentSafety`,影子欄位 `PipelineOutput.safety`、eval 分群、卡片欄、`resafety` 離線工具;**groundedness 經三輪量測否決為路由訊號**——中文理由放行案 ungrounded 中位數 0.42(門檻 0.15),API 對推論句與條款定義詞無鑑別力、C066 真陽性在加條款 source 後消失;四類內容掃描 72 案全 0;不開 SPEC-w;**ADR-009 accepted 2026-10-05**:E1 API 決定性、E2 QnA 逐主張三判準全敗、E3 Prompt Shields 中文注入漏抓而既有抽取→Gate 邊界已吸收注入 → Shields 亦不接主管線)
- [x] Logic Apps 人工覆核流(email 卡片;SPEC-r 2026-10-03:程式端確定性渲染 → HTTP 觸發 → Outlook.com 寄信 → 同步 Response;桌機真寄 C003 收到、錯 URL 401 / 未授權 502 皆由管線記 error 不中斷;Teams 依 §7 砍法不做)
- [x] Container Apps Job 編排上雲(SPEC-t 2026-10-04:無金鑰 Entra(DI Bearer / AOAI token provider)、ACR + 使用者指派身分 + RBAC、DI 快取與輸出走 Blob、`imageTag`=git SHA;首跑 run `20261004T0647-3b5e5c8` 機制全過,品質露出 C036 漏行 / C066 既往誤拒 → SPEC-u 兩道 Gate 檢核補洞;T-M4 撤角色失敗路徑、成本未驗)
- [x] ~~合成資料擴至 40–60 案;eval set 凍結~~(已於 Phase 0 完成:40 案,2026-09-01 人工檢核後凍結)
- [x] 完整基準:三分類 accuracy / 引用正確率 / 分流品質 / 成本每案(2026-10-07,EVIDENCE 第三十九部分:近期 5 輪區間,Gate 後 0.726–0.792、放行錯 0、危險錯 0、每案 USD 0.030)

### Phase 3:包裝與變現(第 6–8 週)
- [ ] ~~App Insights 儀表整理~~ + 成本月報截圖(2026-10-11 縮小範圍:程式本身無遙測(G3),儀表無內容可呈現 → 只截成本分析與 Job 失敗告警,拆除前完成)
- [x] ADR 定稿(英文)— ADR-001~010 全英文(2026-10-19 ADR-009/010 英譯);E11 Agent Service 邊界寫入 ADR-008 附錄
- [ ] GitHub Pages 專案頁(中英)— 檔案已完成(`docs/index.html` / `index.en.html`);**啟用待 repo 搬至主帳號後執行**
- [x] Demo 影片 — 2026-10-16 上傳 https://youtu.be/emzxXhkF_Tk
- [ ] 英文文章 #1(2026-10-11 改列選做);Build Log 收尾統計
- [x] Bicep 全拆驗證(拆除 → 重建 → demo 可跑,計時)— 2026-10-08 ⑧:拆除 27 分、重建遇 3 個真部署坑後完成,事件觸發 + 告警真實觸發驗證(EVIDENCE 44);v16 / v17 全量於重建後環境跑完
- [ ] **重建 Runbook + 預期漂移清單**(支撐「平時 $0、面試前重建」模式,見 §10 交付物)— 重建步驟已實測並記於 `docs/runbook.md`(桌機坑 W1–W25);剩「長期停放漂移清單」獨立段落

**收尾清單(2026-10-11 定案,依序)**:①SPEC 回填 ✓ ②拆除前:成本 / 告警截圖、Demo 影片 ✓(2026-10-16 上傳 https://youtu.be/emzxXhkF_Tk)③全拆 ✓(2026-10-16,15:09,EVIDENCE 49;B6 費用歸零截圖待隔日) ④Runbook 漂移清單 ✓(2026-10-16,`docs/runbook.md` §7:預檢指令 + D1~D17 + 重建驗收) ⑤ADR-009/010 英譯 + E11 ✓(2026-10-19) ⑥公開前掃描 → repo 搬主帳號並 public(使用者操作)→ Pages 內容更新並啟用 ⑦Build Log 收尾統計 ✓(2026-10-19,`docs/build-log.md` 末段;文章選做)

### Phase 3 補強計畫(2026-10-07 盤點;成本為估計,實際以帳單為準)

**F 功能瑕疵(已量測)**
- F1 〔2026-10-11:定為設計取捨,不處理〕人工改派偏多:manual precision ≈ 0.33、Gate 後 accuracy 0.726–0.792(設計取捨,但為實際人力成本)
- F2 〔✓ E9 根因分析 + SPEC-ae / ai 補檢核〕恆錯 6 案:C009、C021、C041、C058、C064、C067(17 輪全錯,未分析根因)
- F3 〔✓ SPEC-aj 程式填引文,v17 引文抄錯 0〕引文改寫:Gate 改派正確答案 105 案次中 67 為 citation_quote_mismatch,多為模型真改寫;放寬經固定解法 30 否決,根治需程式填引文
- F4 〔列為已知限制:真值無案例〕已知檢核缺口:POL-A 第9條門診次數(缺口 2)、POL-A 多段住院之手術 / 加護日數(真值無案例)
- F5 〔部分 ✓ SPEC-ak 輕 / 重雜訊兩級;手寫 / 版面變異列為已知限制〕資料代表性:73 案全合成、3 張示範保單、乾淨 PDF,未測掃描 / 手寫 / 版面變異(最大外部效度風險)
- F6 〔✓ SPEC-am 覆核閉環,未換 ACS〕人工覆核半條:僅寄出摘要信(Outlook.com 第 6 封即封鎖),覆核結果不回寫

**G 「Azure 全託管」聲稱落差(2026-10-07 查 repo 確認)**
- G1 日常執行在桌機:17 輪中雲端 Job 僅 cloud1 / cloud2 兩輪 → ✓ 2026-10-08 v16 全量以事件觸發在雲端跑完(EVIDENCE 45)
- G2 無事件驅動 → ✓ SPEC-ah:Blob `requests/*.json` → Event Grid → Queue → 事件 Job,10/08 真實觸發(EVIDENCE 43/44)
- G3 〔程式遙測列為已知限制〕可觀測性為 0 → ✓(部分)Log Analytics + App Insights + Job 失敗告警(真實觸發 6 分鐘到信);程式本身仍無遙測
- G4 〔待 E11 於英文 ADR 如實表述〕Agent Service 使用淺:僅承載顧問模式 Repair(預設關);角色間路由依 ADR-006/008 刻意留程式——對外敘事需如實表述
- G5 拆除 → 重建未驗證 → ✓ 2026-10-08 ⑧:拆除 27 分、重建遇 3 個真部署才出現的坑後完成;重建當日基礎設施費用近 $0(EVIDENCE 44)
- G6 Cosmos DB 已部署但程式零引用 → ✓ 2026-10-07 移除(ADR-010;實體於 ⑧ 拆除時消失)
- G7 人工覆核非閉環(同 F6)→ ✓ SPEC-am

**E 待評估 / 嘗試項目與估計成本**
| 代號 | 項目 | 補 | 估計成本 |
|---|---|---|---|
| E1 | 拆除 → 重建計時 + demo 10 案 | G5 | 部署 ≈ $0;跑案 ≈ NT$10;DI custom neural 重訓費用未驗證(需查定價) |
| E2 | Event Grid 訂閱:Blob 上傳 → 觸發 Job | G1、G2 | 每月前 10 萬次操作免費 ≈ NT$0 + 每次跑案費 |
| E3 | Application Insights / Log Analytics 接入 | G3 | 每月 5 GB 免費額度內 ≈ NT$0–30 |
| E4 | 覆核閉環:Logic Apps 核准信按鈕 → 結果寫回 Blob / Table(需先換 ACS Email) | F6、G7 | Logic Apps 按次 ≈ NT$0;ACS Email 費用未驗證 |
| E5 | Cosmos:移除,或改用於存覆核結果 | G6 | ✓ 帳單確認 <NT$0.01 → 移除(ADR-010) |
| E6 | 魯棒性:噪聲 / 掃描化 PDF 變體重跑 DI + 管線 | F5 | 每輪 73 案 ≈ NT$100–300(DI 單價待查) |
| E7 | 雲端 Job 平行化全量 | G1 | ≈ NT$70 / 輪;受 50K TPM 限制 |
| E8 | 程式填引文(架構改動,需方案提案) | F3、F1 | 開發 $0;驗證全量 ≈ 3 輪 × NT$70 |
| E9 | 恆錯 6 案根因分析(歷史輸出離線) | F2 | $0 |
|  | **E9 結果(2026-10-07)**:6 案為 Gate 前恆錯,Gate 後 5 案被接住(C009/C021/C041/C064/C067 皆由專屬檢核改派,17/17 或 16/17 正確);Gate 後恆錯為 C058、C059、C061——C061 為已記錄代價(第23條 90 日窗口提醒);C059 為 DI `issue_date` 信心恆 < 0.65 致正確答案也轉人工;**C058(POL-C)與 C049(POL-A)第24條「等待期內發病延續」無專屬檢核**:模型 29 次判 approve,全靠無關旗標(收據項目無法分類 / 90 日窗口提醒)轉人工 = 運氣型,危險錯風險 | | |
| E10 | 同義數字字(仟/千)納入引文正規化 | F3 | $0(離線重放驗證) |
| E11 | Agent Service 敘事定稿(ADR 英文版內如實說明邊界) | G4 | $0 |

**最省成本處理順序**(原則:先 $0 離線 → 再免費額度內的雲端改動 → 重建一次一併驗證 → 最後才是按量計費的全量 / DI)
1. **$0 離線**:E9 恆錯分析 → E10 引文正規化 → E11 + ADR 英文定稿 → 重建 Runbook / 漂移清單初稿(Phase 3 原項)
2. **$0 帳務確認**:E5 查 Cosmos 實際計費,決定移除或轉作覆核儲存(移除屬不可逆,需使用者確認)→ ✓ 移除(ADR-010)
3. **免費額度內 Bicep 改動(先寫好、尚不部署)**:E3 監控、E2 事件觸發,模組改為不綁 `enablePhase2` 的獨立開關
4. **E1 拆除 → 重建一次**:以含 E2 / E3 的 Bicep 重建,同時驗證「可拆可建 + 事件驅動 + 可觀測」;重建後上傳 1 案觸發 Job 驗證(≈ NT$10–50)
5. **Demo 影片 / App Insights 截圖 / 成本月報**(Phase 3 原項,沿用第 4 步環境)
6. **按量計費、需拍板才做**:E7 雲端全量(NT$70)→ E8 程式填引文(≈ NT$210)→ E6 魯棒性(≈ NT$100–300)→ E4 覆核閉環(ACS)
7. **全拆**:Phase 3 交付完成後回到 $0 常駐

#### 分層準確率與決策地圖(2026-10-07;近期 5 輪 v10–v13、v15 共 363 案次,現行 Gate 離線重放)

**L1 模型判定(Gate 前)**:應核付 151/203(74%,錯法以金額錯 50 為主)、應拒付 71/115(62%,誤判核付 43)、應人工 24/45(53%,誤判核付 21);合計 246/363(68%),模型錯 117 中 114 為誤判核付

**L2 Gate 規則族群**(接住 = 觸發時模型確實錯;誤攔 = 模型對且為唯一轉人工理由)
| 族群 | 觸發 | 接住 | 誤攔 |
|---|---|---|---|
| 金額算式反查 | 64 | 64 | 0 |
| 等待期 / 時效(改派拒付) | 26 | 26 | 0 |
| 收據對照 | 18 | 18 | 0 |
| 語意提醒(90 日 / 未列表手術 / 既往症 / 自費) | 34 | 32 | 2 |
| 引文反查 | 26 | 12 | 13 |
| 抽取信心 | 16 | 7 | 9 |
| 理由矛盾 | 3 | 1 | 2 |

**L3 最終分流**:自動核付 133/133 正確、自動拒付 94/94 正確、直通率 227/363 = 62.5%;人工 136 = 真值人工 45 + 攔下模型錯 70 + 誤攔正確答案 21(15%)

**決策地圖**:收件(⚠ 手動觸發、僅乾淨合成 PDF)→ L0 DI + gpt-4.1-mini 抽取(⚠ C059 `issue_date` 信心恆低)→ L1 程式算 derived_facts → L2 gpt-4o 判定 → L3 程式加總 → L4 Gate 42 檢核(⚠ 引文誤攔 13、信心誤攔 9、C061 附錄二提醒恆誤攔;⚠ SPEC-ad 歷史觸發 0;❌ 第24條等待期內已診斷無專屬檢核,C058/C049 29 次靠無關旗標)→ L5 分流:自動核付(❌ 無放行抽樣稽核)/ 自動拒付 / 人工佇列 → 摘要信(Outlook.com 第 6 封封鎖;❌ 覆核結果不回寫)；全程 ❌ 無遙測、❌ 重建未驗證、Cosmos 未使用(→ 2026-10-07 移除,ADR-010)

**風險排序**:🔴 第24條缺檢核、🔴 無放行抽樣稽核 → 🟠 引文 / 信心 / C061 誤攔(人工佇列約 15% 多餘)、SPEC-ad 未實證、資料代表性 → 🟡 覆核不回寫、手動觸發、無監控、重建未驗證

#### 驗證成本原則(2026-10-07 定案)
- **Gate / 程式規則改動**:不改模型輸出 → 以歷史輸出離線重放驗證,$0;可一次批量多項,影響依旗標名逐條分開計算
- **prompt / 模型改動**:需真呼叫 → 只跑受影響的目標案子集(例:10 案 × 3 次 ≈ NT$30),目標案與指標互不重疊者可合併同輪;**全量只在一批改動最後跑一次作回歸**(≈ NT$70),不再「每改一項跑一輪全量」
- 背景:v14 合併驗證兩項 prompt 改動致無法歸因;解法是子集分離 + 末端單次回歸,而非逐項全量

#### 改善流程(2026-10-07 定案;簡 → 難、$0 → 付費)
| 階段 | 項目 | 驗證 | 成本 |
|---|---|---|---|
| 1 $0 程式批量 | ①第24條等待期內已診斷檢核(C058/C049)②放行抽樣稽核 ③C059 `issue_date` 不參與信心分流(確認無判定用途)④引文正規化同義數字(仟/千) | 一次 gauntlet + 17 輪離線重放,逐旗標計效果 | $0 |
|  | **階段 1 結果(2026-10-07,SPEC-ae)**:①②④ 完成、③ 撤回(C059 放行錯 8);新缺口「合併限額少付」已由 SPEC-af `calc_stay_cap_merged` 補上(17 輪觸發 11 全為 C059 真錯、誤旗標 0) | | |
| 2 $0 需真值 | ⑤C061 附錄二特定疾病 → ICD 對照表(使用者標註)轉 Gate 規則 | 離線重放 | $0 |
|  | **階段 2 結果(2026-10-07,SPEC-ag)**:附錄二 ICD 對照上線(使用者確認)、issue_date 豁免重試成功;17 輪 Gate 後正確 871→880、放行錯 0 | | |
| 3 近 $0 雲端 | ⑥Bicep 監控 + 事件觸發 ✓(SPEC-ah)⑦Cosmos 計費確認 / 去留 ✓(移除,ADR-010)⑧拆除 → 重建一次一併驗證 ⑥ ✓(EVIDENCE 44) | 重建後 1 案觸發 | ≈ NT$10–50 |
| 4 低成本單次 | ⑨全量 v16:一次回歸驗證 1–3 全部改動 + 新檢核線上首跑 ✓(雲端事件觸發;Gate 後在區間、C031 缺口 → SPEC-ai,EVIDENCE 45/46) | 全量 1 輪 | ≈ NT$70 |
| 5 需拍板 | ⑩程式填引文(子集 ≈ NT$30 + 併入下次全量)⑪魯棒性(掃描化 / 噪聲 PDF)⑫覆核閉環(需 ACS Email) | 子集 + 回歸 | ≈ NT$150–450 |

## 12. 範圍護欄(不做的事)

1. 不做真實保險商業邏輯的完備性 — 條款庫是教學級虛構,追求「審核流程的工程正確」而非「精算正確」
2. 不自架任何模型 / 不租 GPU — 全託管是本專案的命題,混入自架即稀釋敘事(自架敘事屬 SelectRAG)
3. 不做華麗前端 — 審核 UI 夠 demo 即可,重心在工作流與 ADR
4. 不追多語/多險種擴展 — 範圍蔓延即砍(先砍險種,再砍 Teams 整合留 email)
5. 公司資源零使用(時間/設備/帳號/資料);合成資料不得參考公司內部文件格式
6. 預算硬上限 NT$5,000;App Insights 週檢一次成本
7. 不做補件流程與真實掃描件的頁面分類(2026-10-11 定案)— 文件存在性以固定檔名判定(`app/pipeline/intake.py`),合成資料下不會誤判;補件需要狀態機、補件時效、對保戶通知與重跑機制,屬保險作業流程而非託管技術,且真實文件與零 PHI 定位衝突。文件不全案輸出 manual_review 但不產覆核卡片、不進摘要信(維持現狀,方案 A);否決 B(摘要信加「待補件」行)與 C(表單加補件選項)

## 13. 開發環境分工

| 環境 | 職責 |
|---|---|
| GitHub repo(個人帳號) | **唯一真相源**;定案決策回寫本檔 |
| 雲端對話(claude.ai) | 設計討論、ADR/文章潤稿、合成資料生成輔助 |
| 開發機 + Claude Code | 實作、部署腳本、eval;讀內部開發規則 + 本檔取得全部脈絡 |
| Azure Portal / CLI | 資源管理一律走 Bicep,Portal 僅查閱與除錯(IaC 紀律) |

**同步紀律**:雲端對話產生的結論若未回寫本檔,視同不存在;Claude Code 開工前先讀本檔「已定案決策」與「進度」區。

## 14. 已定案決策(隨開發回填)

- 2026-08-30:立案。題目=醫療保險理賠多代理審核;全 Azure 託管;資料全合成零 PHI;預算上限 NT$5,000;Search 從 Free tier 起步;先單 agent 骨架再多代理化
- 2026-08-30:採「拆建即成本控制」營運模式 — 專案完成後全拆($0 常駐費),求職期以 Bicep + 重建 Runbook 重建當 demo;重建 Runbook + 預期漂移清單列為 Phase 3 交付物;demo 影片為重建不及時的保險。否決替代方案:常駐低配(仍有固定月費,且與成本工程敘事矛盾)
- 2026-08-30:開發順序採「零成本前置優先」(Phase 0)— 資料/schema/Gate/eval/prompt/Bicep 全部在建 Azure 資源前完成,計費窗口壓縮至整合調校期;此順序為成本工程手法之一,入 ADR
- 2026-08-30:claim schema 採方案 B — Pydantic v2(釘 2.9.2)為單一真相源(`app/schemas/claim.py`),JSON Schema 由 `to_structured_output_schema()` 生成並收斂為 structured outputs 子集(additionalProperties=false、全欄位 required、Decimal→string);否決方案 A 手寫 JSON Schema(多真相源必漂移)。轉換層已有 9 項測試守住;真 API 相容性留 Phase 1 驗證
- 2026-09-01:eval set 凍結 — 40 案真值經使用者逐案檢核(34 案通過、6 案依檢核意見修正:C007 補正條號、C026 保單待確認、C031 手術定義論證、C033–C035 申請金額),凍結為評測基準;此後改動需記錄理由
- 2026-09-01:POL-C 病房費限額語意定案 — 依第8條文義採「逐日限額」;合成資料以單一日額生成,總額式 min(病房費總額, 3000×日數) 為其等價計算,推導式一律用 min() 表述
- 2026-09-08:條款語意三項定案(第五批案件檢核時抓到)— ①POL-A 第13條 90/180 日限制**含第8條 ICU 日數**(ICU 日依第8條計入第7條住院日數,故同受限;否則年度上界無界);②「出院後十四日內」起算約定:**出院日不計,翌日起算第 1 日**(C008/C055 第 10 日合併、C059/C060 第 15 日不合併,第 14 日邊界案另補);③Gate 金額上界定位為 **sanity check 而非嚴格上界**,按「案基底 + 每段住院加項 × 段數」參數化(`app/gate/checks.py::amount_upper_bound`),因第10條手術金每次住院各取最高一項、上界隨段數增長不是閉式
- 2026-09-08:schema 缺口 3 定案 — 他保已給付放 `ClaimApplication.other_insurer_paid`(申請書「已向其他保險申請/已獲理賠金額」欄),依 POL-C 第8~11條四桶分項(病房/手術/雜費/救護車)+ `unallocated`,第14條「先逐桶扣除、再套限額」;收據影本戳記為 `MedicalReceipt.is_copy`;他保給付無法歸桶 → manual_review。否決:放收據(收據無他保資訊)、以收據行項目名稱對齊(字串比對必失敗,固定解法 6)、只放總額(條文要求逐項);缺口 1(第四類文件 incident_statement,僅警方紀錄類 4 案)排下一步;缺口 2(第9條門診)暫緩至 Phase 1 有分數再議
- 2026-09-08(2026-10-02 補記:已實作——schema 可為 null、runner 注入陳述文字、4 案資料就緒,八輪全量涵蓋):schema 缺口 1 定案(1b)— 新增選擇性第四類文件 `ClaimBundle.incident_statement`(`source` ∈ police_record / accident_certificate / patient_statement),承載酒駕、無照、犯罪等**非醫療事實**,診斷證明書只載醫療事實;Intake 不列為應備、缺席為 null 而非缺件(Gate「缺席 ≠ 低信心」、Extraction「缺席文件不處理」先行落地);Adjudication 矛盾判定加「沉默不是矛盾」原則;改寫僅警方紀錄類 4 案(C037/C042/C050/C062),美容目的(C043)留在診斷書(醫療相鄰事實)。否決 1a(留在診斷書摘要:會造成 Extraction 的 train/test mismatch——真實診斷書不會寫酒測值)
- 2026-10-03:**ADR-008 定案**——agent 按「驗證邊界」拆分而非知識領域(Policy 獨立 agent 探針 recall 0.772 否決);LLM 修補層**不得擁有放行權**(Repair R1/R2 兩輪:Gate 後 +9/+7 案但各放行一件錯金額、R2 另把應核付改拒付,每件錯都對應 Gate 洞)→ 降為顧問模式(`RepairRecord.would_release`,`applied` 恆 False),承載於 Foundry Agent Service(角色內工具迴圈;角色間路由留程式,否決 connected agents);ADR-006 修訂一條:程式可確定成立的拒付事由(等待期/時效,生效日信心 ≥ 門檻)改派 **deny**(八組 40/40 真值 deny、危險錯 0);新增分段逐日限額檢核(C055 洞);拆行合併為 opt-in(v7 C069 露漏行洞)。否決:Repair 第三輪、LLM 編排、為形式把判定呼叫搬上 Agent Service、程式自動補第11條行
- 2026-10-11:**收尾清單定案**——補強計畫 F/G/E 除 E11 外皆已完成或列為已知限制(F1 設計取捨、F4 無真值案例、F5 手寫/版面、G3 程式遙測);Phase 3 原規劃中「Bicep 全拆驗證」已於 ⑧ 完成、「App Insights 儀表」縮為成本 / 告警截圖、英文文章改選做;新增「repo 搬主帳號並 public」為卡關項。補件流程列入 §12 第 7 條不做
- 2026-09-01:專案名定案為 **ClaimCrew**(否決 MedClaim-Agents / ClaimGate:repo、README、7 篇 ADR、專案頁與所有 commit 歷史皆已用此名,改名成本遠高於收益)

## 15. 進度(隨開發回填)

- [x] **定名 + repo 初始化**(2026-08-30):專案名 ClaimCrew、`development` 分支、內部開發規則個人版、SPEC 入 repo
- [x] **Phase 0 零成本前置**(2026-08-30 ~ 09-01,日曆 3 天;工時約 15 h ≈ 2 個 8 小時工作日,含 9 月真值批次;Azure 支出 $0):見 §11 Phase 0 六項全數完成
- [x] **Phase 1 端到端骨架**(2026-09-30 ~ 10-02,日曆 3 天;工時約 26 h ≈ 3 個 8 小時工作日):部署 ✓、DI custom neural ✓、Search ✓、單 agent 端到端 ✓、真實 eval 八輪 ✓;Azure 支出見現況快照
- [x] **Phase 2 多代理化與防護**(2026-10-03 ~ 10-11;工時約 58 h ≈ 7 個 8 小時工作日):§11 Phase 2 各項 ✓;另含補強計畫 SPEC-ae~am(事件觸發、拆建重建、程式填引文、雜訊 PDF、覆核閉環),v17 全量回歸 J1–J6 全過(EVIDENCE 48)
- [ ] Phase 3(進行中,見 §11 收尾清單)

**工時口徑**(2026-10-13):以 `development` 分支 commit 時戳估算——相鄰 commit 間隔 ≤ 60 分鐘為同一段、每段加 30 分鐘、8 小時 = 1 天;敏感度區間 Phase 2 為 49~72 h;未計無 commit 的桌機等待時間。對外(影片、Pages、文章)一律用此口徑;Phase 0 ≈ 15 h、Phase 1 ≈ 26 h、Phase 2 ≈ 58 h;三階段合計約 99 h,對外總數寫「≈ 100 hours of hands-on, AI-assisted work (estimated from commit history)」,定位為**下限**(雲端 Job 等待、桌機操作未計),不另加無紀錄的思考時間

### 現況快照(2026-10-02,Phase 1 結案)

| 項目 | 數量 / 狀態 |
|---|---|
| 保單條款庫 | 3 份 / 100 條(MD + PDF) |
| 合成理賠案 | 72 案(approve 40 / deny 23 / manual_review 9),真值已凍結;2026-09-07 C009 改案、C041 新增;2026-09-08 第三批 C042–C053(除外責任 / 但書陷阱 / POL-B 計算)、第四批 C054–C058(計算型條文 / B、C 等待期)、第五批 C059–C065(反例組 / 年度 180 日 / 犯罪行為 / 診所定義)、第六批 C066–C071(第 14 日邊界 / 申請時效 / 剖腹產 / 非特約醫院 / 他保扣除 ×2)、C072(他保逐桶)經人工檢核加入 |
| DI 訓練標註 | 71 份(PDF 不入庫,`di_labels --assemble` 自 data/claims 組裝上傳集) |
| 測試 / 型別 / lint | 868 passed、coverage 86%(floor 80)/ mypy strict 0 / ruff 0 / 變異 41+5+6 全殺(CI 全綠;最新數字以 `bash tools/gauntlet.sh` 輸出為準) |
| 英文 ADR | 8 篇(ADR-007 accepted 2026-10-02;ADR-008 accepted 2026-10-03:驗證邊界拆分 + 修補層顧問化) |
| Gate / eval | 31 種旗標;終版 Gate 對七輪真實輸出 504 案次放行 161 件金額全對、危險錯 0;`tools/regate.py` 離線重跑 |
| **Azure 累計支出** | Phase 0 $0;Phase 1 **NT$399.81 入帳至 10/1**(Cost analysis 2026-10-02 截圖;09-30 部署日與 10/1 四輪全量,全為 gpt-4o 用量,Storage/Cosmos/頻寬各 <NT$0.01、Search NT$0);10/2 五輪全量(core 補 16 + v4~v8)尚未入帳,依 10/1 單價推估約 NT$500~600(非實測,入帳後回填);11 資源閒置成本零頭級(Cosmos serverless、DI/Search/CS 免費層) |
