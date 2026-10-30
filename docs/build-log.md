# Build Log — ClaimCrew

> 逐日記錄:完成里程碑、卡點與解法、Azure 成本狀態。快速交付敘事的證據(SPEC §7)。

## Day 1 — 2026-08-30(Azure 成本:$0)

- repo 初始化:development 分支、內部開發規則(未公開)、SPEC 入 repo、README(成本優先路線圖)
- 架構決策 ×3 定案:拆建即成本控制模式、Phase 0 零成本前置順序、claim schema 方案 B(Pydantic 單一真相源)
- Phase 0 開工:專案骨架 + claim schema 實作(三文件模型 + 判定輸出 + structured outputs 子集轉換層)
- 證據優先流程 gauntlet 回補:13 tests / coverage 100% / mypy・ruff 全綠 / 手動變異 6/6 killed(M3 首輪存活揭露測試盲點,補直接單元測試後復殺——詳見 devlog 2026-08-30)
- 工具鏈釘版入 `requirements-dev.txt`;`tools/gauntlet.sh` 一鍵重跑全層

## Day 2 — 2026-08-31(Azure 成本:$0)

- 條款庫完成:POL-A(住院 36 條)/ POL-B(手術 30 條)/ POL-C(實支實付 34 條),陷阱點刻意內建(等級表交叉、除外中帶例外、雜費負面列舉)
- 首批 10 案 + 提議真值(附推導式);住院日數慣例定案「含入出院日」並重算四案
- eval v1(三分類/混淆矩陣/金額 MAE,--selfcheck 滿分)、程式側 Gate(條款索引反查/引文一致/金額上界/信心分流,27 測試綠)
- 四 agent prompt 草稿、Bicep 全套模板(bicep build 0 errors)、GitHub Actions CI 首跑即綠(2/2 success)
- Phase 0 僅餘:案件擴量 40–60、PDF 排版、DI 訓練樣本、ADR/專案頁骨架

## Day 3 — 2026-09-01(Azure 成本:$0)

- 案件擴量至 40 案(C011–C040 模板×參數生成,真值由條款參數同源計算器產出);PDF 全量渲染、DI 訓練樣本 39 份
- **真值人工檢核**:40 案逐案核,6 案 x + 8 項建議,修正後凍結 eval set(詳見 devlog 2026-09-01 ❌ 失敗紀錄)
- 英文 ADR ×7、專案頁中英雙檔(沿用作品集版式)、devlog 與截圖素材、SPEC 進度區同步、專案名 ClaimCrew 定案
- Phase 0 六項全部完成

## Day 4 — 2026-09-02(Azure 成本:$0)

- 全專案稽核對照 SPEC,列出 15 項可改善(稽核清單已於公開前移除,修正內容見各 commit)並依序修正:
  補 Intake/Policy 輸出 schema、eval v2(評 Gate 後判定 + 引用正確率 + 分流品質)、Gate 條號正規化、
  PDF 位元組穩定、資料不變量測試 244 條、gauntlet 擴為五層(隨機順序/供應鏈審計/變異)、Bicep 補齊 §8 十項服務、
  README/build log/內部開發規則進度回填、DI 訓練 PDF 不再重複入庫(`--assemble` 組裝)、
  schema 診斷書改 `admissions` 列表(含 ICU;C008 兩段住院可表達,40 案重生成、真值零變動)
- 供應鏈審計首跑即抓到 3 個已知漏洞(pytest、pdfminer-six),升級後 293 tests 回歸通過
- ❌ 變異腳本以 `git checkout` 還原目標檔,把尚未 commit 的 schema 修改一併抹掉、推上去的 commit 缺 schema;
  改為執行前副本還原並補 INT/HUP trap(第二輪稽核三個視角各自獨立抓到)

## Day 5 — 2026-09-07(Azure 成本:$0)

- 第二輪稽核 22 項(A16–G37)依序修正:schema 補 `policy_effective_date`、claim.json→ClaimBundle 轉接層(生成器落盤前契約驗證、40 案入 schema 測試)、
  Gate 兜底掃抽取信心、Adjudication/Intake/Extraction prompt 補多段住院/ICU/限額 min()/應備四項/icu_days 來源、
  pyproject.toml(mypy strict、ruff 六規則、coverage floor 80)、gauntlet 加 selfcheck 層、CI 補 permissions/concurrency/cache、
  datagen 直接單元測試(coverage 61%→84%)、POL-A 金額上界修正 683000→503000 並以測試鎖定與條款參數同源
- 凍結真值改動(記錄理由):C009 原「未列表手術」與 C040 重複,改為第二個「收據與診斷不符件」(POL-A,收據病房費 5 日 vs 診斷書住院 2 日 → 補正轉人工);分布 23/10/7 不變;人工檢核通過並依意見補條號與推導式,C007 推導式同步重寫
- 新增 C041(POL-C 收據「處置費」性質不明 → 第26條專條 → 人工):「收據與診斷不符」三案三條路(B 直接適用 / A 目的性擴張 / C 專條);41 案、分布 23/10/8

## Day 6 — 2026-09-08(Azure 成本:$0)

- 第三批案件 12 案(C042–C053)以提案總表交人工檢核:11 v、1 x(C047 與 C038 同型,依意見換成 POL-B 第10條同部位十四日內再手術);
  依檢核意見調整:C046 申請額改 17,000、C052 補慰問 34,000、C048/C053 摘要關掉酒駕/無照條、C051 術式對齊附錄一、C053 推導式補第10條「最高一項為限」層、C044 移除嬰兒室費
- 補的是覆蓋缺口:POL-C deny 0→3(酒駕/美容+非健保陷阱/分娩)、POL-B deny 1→4(牙科/既往症)+ POL-B 第9、10條算式、
  四個但書/例外陷阱 approve(子癲前症、傷害免等待期、牙科之意外例外、同部位再手術)、POL-A 第24條等待期內發病延續(首例)
- 53 案,分布 28/17/8;整批 --force 重生成驗證既有 41 案位元組零變動;446 tests / selfcheck PASS
- 檢核另列六個尚未覆蓋的計算型條文(POL-A 第10條多項手術鏡像、POL-C 第14條他保扣除、第13條雜費合併、B/C 疾病等待期原則、POL-A 第9條門診時間窗),留作第四批
- 第四批 5 案(C054–C058)提案總表交人工檢核:5 v;依意見補 C058「不適用附錄二、拒付依第24條非第23條」、C055 摘要補自費抗生素;
  補的是計算型條文:POL-A 第10條多項手術「最高一項為限」(與 C051 同收據只換保單,40,000 vs 42,500)、POL-C 第13條雜費限額合併、B/C 疾病等待期原則、POL-C 第24條(含「POL-C 無附錄二」跨保單陷阱)
- 58 案,分布 30/20/8;整批 --force 重生成驗證既有 53 案位元組零變動;481 tests / selfcheck PASS
- 檢核另列第五批候選:兩段住院「不合併」反例(C008/C055 各配一個)、POL-A 第13條年度累計 180 日、犯罪行為除外、POL-A 傷害免等待期 approve、POL-A 第10條未列表手術、POL-B 診所定義;
  兩個需 schema 新欄位的缺口(POL-A 第9條門診日期清單、POL-C 第14條他保逐項給付)另提設計方案
- 第五批 7 案(C059–C065)人工檢核 7 v;依意見:C059/C060 推導式寫死「出院日不計,翌日起算第 1 日」、C061 摘要補跌倒與保守治療、C062 事實來源改「病患自述」、C065 補治療必要性
- C061 揭露 Gate 上界缺陷:年度 180 日 + 每段住院各取最高手術金,上界隨段數增長非閉式 → 改為「案基底 + 每段加項 × 段數」參數化;
  SPEC §14 同步定案三項條款語意(ICU 日受 90/180 限制、十四日起算約定、上界為 sanity check)
- 65 案,分布 35/21/9;整批 --force 重生成驗證既有 58 案零變動;531 tests / selfcheck PASS
- 待決 schema 設計(另提方案):POL-A 第9條門診日期清單、POL-C 第14條他保逐項給付、第四類文件(事故證明/病患聲明)承載非醫療事實
- commit 0:Gate 低信心掃描「缺席 ≠ 低信心」(optional None 跳過)+ Extraction 缺席文件不處理守門
- 缺口 3 落地:`other_insurer_paid` 四桶進申請書、`is_copy` 進收據,生成器單一處補預設、65 案重生成(真值不變),Adjudication 規則 9a「先逐桶扣他保再套限額」;2 個驗證案(他保付 30,000 / 5,000)待人工檢核
- 第六批 4 案 + 缺口 3 驗證案 2 案人工檢核 6 v;依意見:C066 摘要去膽道引流、C068 推導式定死「但書列舉非例示、第30條不得撐開」、C069/C010 醫院名改「康泰醫院」、C070/C071 戳記改「與正本相符」+ 申請額改毛額 88,000;71 案,分布 39/23/9;整批重生成僅 C010(改名)與新案有 diff;574 tests / selfcheck PASS
- 第七批:C072(他保扣病房桶,測第14條「逐桶」)納入;C073(日額型他保是否落入第14條「已給付部分」屬條款解釋問題,須先在 SPEC 定「扣/不扣」才有真值)、C074(時效反例僅對稱美感且非單變數)依檢核不納入;72 案,分布 40/23/9;581 tests / selfcheck PASS
- 缺口 1 落地(1b):第四類文件 `incident_statement`(optional、nullable anyOf 進 structured outputs 子集並以測試鎖定)、Intake/Extraction/Adjudication prompt 同步、「沉默不是矛盾」原則;C037/C042/C050/C062 的酒駕/無照/犯罪事實移入警方紀錄 PDF,診斷書只剩醫療事實;583 tests / selfcheck PASS

## Day 7 — 2026-09-30(Azure 成本:$0,尚未建立任何資源)

- 桌機環境建置:Python 3.11 venv、釘選套件、Azure CLI 2.90.0 / Bicep 0.47.16;四連檢查全綠(583 tests / mypy 0 / ruff 0 / selfcheck PASS);Bicep 在新版 CLI 冒 BCP318 → 條件式模組 outputs 改 `.?`/`??`/`!`,兩版 0 warnings
- 新增 `docs/runbook.md`:桌機/雲端分工、每次開工指令、Azure 逐步清單、情況→指令速查、桌機坑 W1–W7
- Azure 開通:免費試用帳戶升級 Pay-As-You-Go(訂閱 `claimcrew`,Basic 支援);預算 `claimcrew-monthly` 1,500 TWD/月,Actual 50/80/100% Email 警示;CLI 首次登入需 `--tenant` 觸發 MFA(W6);九個資源提供者 Registered
- 區域 eastus 定案;首次 what-if 被 `ServiceModelDeprecating` 拒絕——gpt-4o 2024-08-06 / 4o-mini 2024-07-18 皆 Deprecating(退役 2027-04-14),GlobalStandard 配額 0、Standard gpt-4o 50K → Bicep 改 gpt-4o 2024-11-20 + Standard 單模型,mini 以 `enableMiniModel` 旗標關閉;ADR-007 記修正
- gpt-5.6-luna(GA、退役 2028-01-11)配額 0,申請入口需訂閱先有 Cognitive Services 資源 → 待首次部署後於 Portal Quotas 送件
- 首次部署 `create` 11 資源 Succeeded(4 分 23 秒);luna 配額申請兩次被拒(自動規則、換區無效)→ 結案,OpenAI 帳戶移至 eastus2(Bicep `openAiLocation`)並宣告 Studio 所需 CORS
- DI 訓練三次才成功、每次都是真 API 校正:①labels 格式被 Studio 原樣接受(掛號 ② 過關)但同值共框(70/71 份)②`Can't find any OCR files`(需 Run layout 產 ocr.json,F0 429 重跑即可)③v2 真實觸發 7/8——摘要只抽第一句,根因是標註跨行退回切段(15/71 份);改逐字座標每行一框 → v3 C008/C055 8/8
- 新增 `tools/di_smoke.py`(REST 首跑即正確);585 tests / mypy 0 / ruff 0
- 待辦:Search 索引 → 單 agent → 首次 eval

## Day 8 — 2026-10-01(Azure 成本:gpt-4o 兩次全量約 $7.5 + Search/embedding 零頭;Cost analysis 延遲一天入帳)

- 條款入 Search(hybrid、104 文件)→ recall@5 0.655 / @10 0.835;量測每張保單僅 ~3.3k tokens → 條款上下文改 A/B(`full` vs `core+search`),核心集標題規則 71/71
- 證據優先流程 SPEC-1 單 agent 骨架:`app/pipeline/{context,intake,di_client,aoai_client,runner}`、`tools/run_pipeline.py`、29 tests;C008 三輪真打校正 structured outputs `$ref` 兄弟鍵、Money 正規化、policy_id 正規化、layout 行信心(各獨立 commit)
- 72 案首次全量:先撞 429(capacity 10K TPM)→ Bicep 50K + Retry-After/6 次/封頂 60s/`--pace` → 72/72、957k+112k tokens;eval 0.139 但 Gate 前 0.778(新增 `pre_gate_accuracy`、`tools/regate.py` 離線重跑 Gate)
- SPEC-2:引文 NFKC+去標點(不符 63→17)、等待期確定性檢核(ICD S/T 豁免、fail-closed)、金額改 LLM 填明細程式加總(`CalcLine`/`AdjudicationDraft`)、prompt 判定順序;v2 全量 Gate 前 0.806、金額對 17/39、citation_precision 0.89
- SPEC-3:`derived_facts` 注入(日數/間隔/距生效日)、Gate 條款金額參考表反查(自 `POLICY_PARAMS`)、日數候選集檢核、`capped_per_unit`;v2 離線:正確 17 案 0 誤旗標、22 案錯命中 17
- gauntlet:719 tests / mypy 0 / ruff 0 / pip-audit 0 / 變異 6+18 全殺、property 子集 5/5(M18 先存活→補不變量);EVIDENCE 一份三部 SPEC
- git 歷史作者統一為 Sean Chuang(桌機 filter-branch 腳本檔方式;PowerShell 多行單引號會吃掉引號);遠端僅餘 `development` / `main`
- v3 全量(SPEC-3 驗收):Gate 前 0.847、approve 列 40/40、金額 31/40;日數型錯誤 ≥8 → 1;剩 9 案金額錯全為結構錯、4 案 deny→approve 門檻擋不住
- SPEC-4:結構檢核(第7⇔11 配對、180 日、每段一項手術、間隔 >14 不合併)、時效二年、POL-A 30~89 日特定疾病窗口、POL-B 雙表示;v3 離線:9/9 金額錯命中、6/7 危險錯擋下;只用確定性旗標時放行 18 案金額 18/18 全對;772 tests / 變異 24+6 全殺 / property 5
- 待辦:門檻拍板(高原 ≤0.65)→ `core+search` A/B 結果 → 引用不符 20 案分類 → 第24條語意旗標評估

## Day 9 — 2026-10-02(Azure 成本:gpt-4o 兩輪 ≈ $7;DI F0 額度已恢復,快取建滿後 DI 用量歸零)

- DI F0 額度:一天後恢復(非月額度);core+search 補跑 16 案 → A/B 完整 72 案:省 6.2% tokens、Gate 前 59 vs 61,`full` 預設定案寫入 ADR-005
- `full` v4 真實執行(0.65 門檻 + 程式量測分流 + 引文片段規則):Gate 後 0.577、放行金額 20/22、deny→approve 2;揭露三個新類型放行錯:手術費/麻醉費拆行各套限額(C003)、POL-B 漏慰問金行(C048)、reasoning 寫拒付但 decision=approve(C046);C064 模型填「ゼロ」整案 error
- A:Money 明示為零白名單 + 模型輸出驗證失敗重試一次;SPEC-f:限額合計 ≤ cap × 段數、POL-B 第7⇔12 配對、判定/推理矛盾關鍵字;三輪離線回歸 0 誤旗標、v4 三案全接住(acc 0.577→0.549、金額 exact 1.0、危險錯 1)
- 820 tests / 變異 29+6 全殺 / property 5;EVIDENCE 第七、八部分
- v5 真實執行:72/72、快取全命中(單案 ~11 s);放行金額錯 3 案全為「十四日內再住院合併」類 → SPEC-g:住院分組(日數候選、雜費限額合併)+ 非健保 65% / 收據日數 / 收據分類 / 既往症 / 未列表手術五個提醒型檢核;四輪離線回歸危險錯全部歸零、放行金額 exact 1.0、0 誤旗標;834 tests / 變異 33+6
- v6 真實執行(SPEC-g 上線後):Gate 前 0.819 / 後 0.569;**放行 19/19 金額全對、危險錯 0、manual→approve 0**;Gate 前判錯 13 案有 12 案被確定性旗標接住——第一輪「放行皆對」的真實執行;代價 approve 列 21/40 轉人工(引文跳項 7、明細表示法 8)
- 首張帳單:10/1 NT$399.80 全為 gpt-4o(輸出 ~37%、cached input ~37%、一般 input ~26%),DI/Search/Storage/Cosmos 合計 <1 元;prompt caching 自動生效於 `full` 的固定條款前綴
- SPEC-h(最後一輪):引文列舉標記為片段邊界 + 附錄表格列範圍 + 格界哨兵(引文有標點處可跨一格);五組離線回歸 v6 0.569→0.611、core 0.611→0.694、放行金額全對、危險錯 0;865 tests / 變異 39+5+6;第一版硬哨兵誤擋 5 筆誠實表格引文,回歸看「降級變多的案」才抓到
- v7 真實執行(SPEC-h 上線後):Gate 前 0.833 / 後 **0.667**(歷次最高)、manual recall 1.0、危險錯 0;放行 26 件金額 25 對,C069 −3,900——手術/麻醉拆行超限被 65% 比例遮蔽(F1 比的是 ratio 後金額)+ 漏雜費行無檢核,兩錯抵消
- SPEC-i:限額合計檢核改比 ratio 前金額(RED 先重現 C069);離線 v7 放行 25/25 全對、六組 0 誤旗標;866 tests / 變異 40+5+6;「漏雜費行」記為已知限制
- v8 真實執行(終版前):Gate 前 0.792 / 後 0.556,退 3 案在雜訊帶內,模型兩件危險翻轉(C022 deny→approve、C007 manual→approve)皆被 Gate 接住;C055 放行少付 22,000——`capped_per_unit` actual 填 0 整行歸零 → SPEC-j 零值行檢核(七組只命中 C055);868 tests / 變異 41+5+6
- **Phase 1 終版數字**(終版 Gate 離線套 v8):Gate 前 0.792 / 後 0.542、放行 18/18 全對、危險錯 0;七組 504 案次放行 161 件金額全對、危險錯 0。EVIDENCE 第十二部分
- **Phase 1 結案** → Phase 2(多 agent 拆分、模型分層路由實測 ADR-007、明細表示法放寬、收據類別覆蓋檢核)

## Day 10 — 2026-10-02(Phase 2 啟動;Azure 成本:$0,本日僅程式)

- Phase 2 拍板:順序 A(ADR-007 分層路由實測)→ C(明細表示法放寬,穿插)→ B(多 agent 拆分,直呼 API 編排);C073 採「不扣」(第14條觸發要件為收據正本已用於他保,日額型不成立);部分核付維持三分類 + 衍生指標;11 資源不拆(Cosmos serverless、閒置零頭級),拆建驗證移到 Phase 2 結案
- SPEC-k(方案 X):逐呼叫 deployment 參數、逐呼叫 token 記帳(含 cached)、`tools/cost_report.py` 價目表計價(表頭印日期來源、用量與金額分欄);880 tests / 變異 45+5+6;假設 1 → gpt-4.1-mini 2025-04-14 Standard(200K)部署成功;假設 3 **推翻**:抽取只佔 prompt 39.1%;10 案配對抽取輸出 0 差異、每案成本 0.0454 → 0.0318 USD(−30%,判定呼叫正規化後);價目表自 Retail Prices API 釘入(regnl 比公開價高 10%);ADR-007 補 Measured outcome;**全量路由臂 route_v1**:Gate 前 0.819 / 後 0.694(歷次最高)、manual recall 1.0、危險錯 0、每案 0.0456 → 0.0302 USD(−33.8%);放行 26 件 1 錯(C051 +7,500)——bundle 兩臂相同,是 4o 判定把次高手術填全額、Gate 因 15,000 同為第二級全額與第三級折半而放行(表示法歧義洞,候選 SPEC-l);**ADR-007 accepted**(抽取預設 gpt-4.1-mini);SPEC-l 補等級對照檢核 `calc_surgery_grade_mismatch`(多重集一對一;八組只命中 C051、0 誤旗標;888 tests / 變異 48+5+6)
- 第三刀 B(多 agent 拆分)設計:離線量測推翻兩個假設——核心集不含除外責任章(三保單各 7 條)、條款全文僅佔判定 prompt 1/3 → X-b 放棄;X-c 品質實驗先以 `tools/policy_probe.py` 量 Policy 呼叫單獨 recall(10 案、< $0.3),門檻 ≥0.90 進 72 案、<0.80 走 Y(ADR-008 正式化單呼叫);893 tests / 變異 51+5+6
- 第二刀 C「明細表示法放寬」量測後**不做**:嚴格定義下八組 576 案次僅 4 案次、3 件為巧合正確(放寬即多付),直接進 B(多 agent 拆分設計)
- 第三刀 B 改題:SPEC-m 探針 10 案 recall **0.772**(第11條輔助金 ×4 漏)→ 按知識領域拆否決;v8/route_v1 旗標案統計(35/28 案:假陽性 9/6、判對金額錯 12/9、真值人工 4/5、方向錯 10/8),「旗標已含正解」型可修補為大宗、第11條成對缺行與探針漏引同一弱點;危險訊號 C035/C060 只有引文旗標卻金額大錯 → 方案 R+S(Repair 以 Gate 為工具的有界迴圈 agent + 引文類旗標案第二意見)。多 agent 原則定為「按驗證邊界拆,不按知識領域拆」;SPEC §11 勘誤(ADR-005 否決的是 Search serverless preview,非 Agent Service)。SPEC-n Foundry Agent Service 探針:`tools/agent_probe.py`(parity/gate 兩模式、strict schema、Gate function tool ≤2 輪、`--agent-ref-style`)、`infra/bicep/modules/foundry.bicep`(AIServices 帳戶/專案/gpt-4.1-mini,`enableFoundry` 預設 false)、runner 抽出 `build_decision_messages` 共用判定 prompt;SDK 自 wheel 查證 azure-ai-projects 2.7.0(Agents v2)否決 classic;907 tests / 變異 56+5+6 / bicep 0 warning;真實執行待桌機(runbook 4.14,P1–P4 門檻)
- SPEC-n 真實執行(10/02 深夜~10/03):p2n 首部署 `RequestConflict`(專案與部署並行寫同帳戶)→ dependsOn 修正;角色名已更名 Foundry User;`agent` 引用 400 deprecated → `agent_reference`;mini 當判定臂爆 4k 輸出被截斷(直呼臂 finish_reason=length)→ 探針單案例外不中斷。**P1 10/10、P3 9/9、P4 修補 7/10 危險錯 0;P2 identical 0/10 經噪音基線校正(直呼 vs 直呼也只 1/10,臂間判定一致 9/10 > 同臂 7/10)視為通過**——Agent Service 機制面全成立。eastus 配額:Standard gpt-4o 50K 未用、11-20 版 Legacy 可部署 → (b) Foundry 搬 eastus 核准
- SPEC-o Repair agent(Tier 3,失效模型 F1–F10):`app/pipeline/repair.py` 觸發判定(提醒型 skip / 純引文 / 可修)+ 六道程式守門(Gate 重跑、禁升級、新增行須被點名且未點名既有行凍結、confidence 不得上升、引文類旗標案第二意見一致)+ fail-closed;`RepairRecord` 落在 `PipelineOutput.repair`(無 --repair 時不落欄位,O13b 抓到 `null` 會改位元組);`tools/rerepair.py` 離線重放、`run_pipeline --repair`;Agent Service 共用層抽 `app/pipeline/agent_service.py`;Bicep Foundry 搬 eastus + gpt-4o 2024-11-20 Standard 50;930 tests / 變異 65+5+6 / repair.py 98%;真實重放待桌機(T1 Gate 後 ≥0.75、T2 放行金額 100%、T3 危險錯 0、T4 真值人工不放行)
- SPEC-o 真實重放(10/03):Foundry 搬 eastus(拆帳戶須先刪專案/部署)、p2o Succeeded;3 案探針抓到 G3 漏洞(改 deny 清空明細被誤擋 → O5b 修);全量 v8 0.556→0.681、route_v1 0.694→0.792、危險錯 0、C035/C060 皆未放行;**但 T1(v8 <0.75)與 T2(route_v1 C003 Repair 把收據 actual 22,000 改 18,000 湊限額、少付 2,000)不過 → R+S 本輪否決**;另抓到平行 function_call 只回填第一個的 400(已修 N17);候選 G7「事實欄位不得縮減」待拍板是否做 R2
- R2(G7 事實不縮水 + 平行呼叫回填,67 mutants):v8 0.653 / route_v1 0.778、危險錯 0,但 C055 +5,000(Repair 修對 kind 後放行、Gate 無分段逐日限額檢核)與 C065 應核付被拒付(Repair 以改 deny 消解理由矛盾)→ **R+S 否決,退回方案 P**;結論:Repair 每件放行錯都對應一個 Gate 洞,把幸運人工轉成放行等於把洞變損失;正向增益屬「旗標已含正解」型,程式確定性處置可得
- SPEC-p 方案 P + 顧問模式(10/03):八組離線重算(worktree 公平基線)——P2 等待期/時效改派 deny(40/40 真值 deny、生效日信心守門)+ P3 分段逐日限額檢核(C055)每組 accuracy 皆升(route_v1 0.681→0.750、v7 0.653→0.736、v8 0.542→0.625)、放行錯不增、危險錯 0;P1 拆行合併修對四件 C003 但 v7 C069 露出漏雜費行(−5,200)→ 預設關、opt-in;Repair 改顧問模式(`would_release`/`applied`)、`rerepair --release` 量測用;**ADR-008 accepted**(驗證邊界拆分、修補層無放行權、ADR-006 修訂 deny 改派);950 tests / 變異 72+5+6;devlog 第三刀
- SPEC-q 收據覆蓋檢核(10/03):`receipt_category_uncovered`——POL-C approve 時收據命中之給付類別(第8條病房/膳食、第9條手術/麻醉、第10條藥/材/檢/復健/雜費、第11條救護車)須有對應明細行,附錄一視同覆蓋第9條(v6 C033 / v7 C035 實證,否則誤旗標 2 件),負面列舉不要求;八組離線重測:只命中 v7 C069(第10條 ← 藥費、檢驗費)、誤旗標 0、P1 合併開啟後放行錯 0 → **P1 拆行合併改預設開**(route_v1 0.750→0.778、v4/v5/v7 各 +1~2 案);pre-mortem 的收據金額差額檢核量測後**不做**(36 個差額命中有 3 件金額正確,差額來自 per_unit 行不帶 actual_amount,訊號不乾淨);957 tests / 變異 76+5+6
- SPEC-r Logic Apps 人工覆核卡片(10/03;ADR-008 顧問輸出的讀者):`app/review/card.py` 確定性渲染(PipelineOutput + ClaimBundle 既有欄位、不新增 LLM 呼叫、全欄位 html.escape、同輸入同位元組)、`should_review` 只送 manual_review(P2 自動 deny opt-in)、`post_card` 同步 POST 容錯 + `sig=` 遮罩;`tools/send_review.py` 離線預覽/送卡、`run_pipeline --review`(run log 新增 `review` 欄位,無 --review 恆 off);Bicep `logicapp.bicep` 改為 Request → Outlook.com 寄信 → Response 200/502(同步,寄失敗管線看得到)、`enableReview` 與 enablePhase2 解耦。**R-M1 dry-run 抓到資料安全洞**:v8 C019/C020/C054 的 LLM relevance/reasoning 寫出病患姓名 → 卡片層以已知姓名值遮罩 `<被保險人>` 後 36 張 0 洩漏、最大 5,356 bytes;**桌機真測(同日)**:部署 Succeeded(+2 資源)、連線未授權時 502 被同步回報(非假 sent)、錯 URL 401 不中斷、Portal 授權後 C003 `sent http=200` 信箱收到卡片 → R-M1~M3 全過,SPEC §11 Logic Apps 打勾;`post_card` 補印失敗回應本文;969 tests / 變異 81+5+6
- **v9 全量(10/03,P1 預設開首次線上 + `--review`)**:Gate 前 0.778 / **後 0.778(歷次最高,前高 0.694)**、放行 **28/28 金額全對**、危險錯 0、manual recall 1.0、每案 0.0304 USD;P1 合併發生 C003/C069/C071 三案全對放行;P2 自動拒付 5 案全對;剩 16 件判錯全為「寧可人工」方向(引文不符 7、POL-A 結構 5)。**送卡 6 封後 Outlook.com 封鎖帳號**(Send `Forbidden`、16 件 502;新信箱數分鐘內多封 HTML 觸發反濫發)→ SPEC-s 摘要信:一輪一封(`build_digest`/`split_digest` 依 1 MB 自動分封、目錄錨點、先落地 `_review_digest.html` 再寄)、`send_review` 預設摘要(`--per-case` 保留)、`run_pipeline --review` 改收集後一次寄、Bicep 觸發 schema 放寬(subject/html 必填)+ 失敗本文帶 `code`/outputs;dry-run v9 1 封 22 案 94.7 KB、v8 36 案 152 KB、姓名 0;順手修 `run_pipeline --help` 崩潰(`%` 未跳脫,既有缺陷)。**10/04 桌機真測**:重部署 Succeeded;S-M3 錯 URL 401 + 檔案落地 ✓;帳號 abuse mode(`AADSTS70000`)時連接器 401 由新 Response 本文直接帶回;帳號解除 + 重授權後 **v9 摘要 1 封 22 案 `sent http=200`** → SPEC-s 結案;10 月累計 NT$1,274(10/1~10/3,全為 Foundry Models;Logic Apps NT$0.14),預算 1,500 第 3 天已 85%
- **SPEC-t Container Apps 編排上雲(10/04)**:質疑 §8「API + 審核 UI」設定——真實使用型態是批次 72 案 + 一封摘要,改為 **Container Apps Job**(手動觸發、scale-to-zero);T1 無金鑰驗證(`CLAIMCREW_AUTH=entra`:DI Bearer / AOAI `azure_ad_token_provider`,key 模式位元組不變)→ **T-M1 桌機真測:個人帳號加 DI/AOAI 資料面角色後 `di_smoke --entra` 8/8、`aoai_smoke --entra` 兩 schema OK,假設 1 成立**;T3 Dockerfile(slim、非 root、烘入 claims/policies/prompts、`.dockerignore` 排除 check/tests/docs)+ CI image job(容器內 selfcheck、非 root、無秘密);T4 `tools/cloud_run.py`(run_meta 先寫 → 拉 DI 快取 → 管線 → 整目錄上傳 → 快取回推不覆蓋 → meta 收尾,例外全記不吞)+ `tools/sync_cache.py`;T5 Bicep:ACR Basic + 使用者指派身分 + 四個 RBAC + Log Analytics + Job(映像 tag 必填 = git SHA、`listCallbackUrl` 直接把摘要觸發 URL 塞進 Job secret 不經人手)、`enableJob` 與 enablePhase2 解耦、Storage 新容器 runs / di-cache;新依賴 azure-storage-blob 12.31.0(核准);M86 entra 仍送金鑰、M87 上傳漏底線檔、M88 meta 不先寫、M89 拉快取丟子目錄(原 M89「回推覆蓋」因雙重防護等價而改題);真部署 / Job 真跑 / T-M4 待桌機(runbook 4.18)
- **雲端首跑 cloud1(10/04,run `20261004T0647-3b5e5c8`)**:機制全過(Job Succeeded、143 檔上傳、DI 快取 207 命中 0 新呼叫、摘要信 1 封、全程 Entra 無金鑰)但品質兩門檻破——Gate 後 0.722、放行 21/22(C036 第10條藥費 12,200 漏行)、**危險錯 1(C066 復發案被引第19條既往症拒付)**;首次 Job 9 秒無聲失敗未重現、根因未證實;T-M4/T-M5 未做
- **SPEC-u 兩洞補強(10/04)**:A′ `receipt_amount_uncovered`(第10條明細 actual 合計低於收據同類別合計且未達限額 → 人工;10 組 0 誤旗標、4 命中全真錯)+ B `pre_existing_unsupported`(deny 引用該保單既往症條款但診斷書無既往記載 → 人工,不改方向;理由關鍵字版因模型固定「③既往症:未見記載」句而否決,改條號觸發;代價 C068/C044/C049 四案次 deny→manual)+ regex 擴充(69 bundle 只 C024 新命中)+ 摘要主旨改 run_id;cloud1 重放危險錯 1→0、放行錯 1→0;1007 tests / 變異 96+5+6;gauntlet 被 SIGTERM 砍一次留 M74 殘留(git diff 抓到)、M95 一次空變異(sed BRE `*`)
- **雲端第二跑 cloud2(10/04,SPEC-u 版 `c9b2fca`)**:19 分鐘、143 檔、快取全命中、主旨帶 run_id;Gate 前 **0.819** / 後 0.694、放行 **25/25 全對**、危險錯 0、manual recall 1.0;C036/C066 本輪模型自己對(抖動),A′/B 線上未被需要;B 代價 C049/C068 轉人工如預期;首次啟動誤用舊映像(`<信箱>` 占位符害部署靜默失敗)→ 停掉重跑,runbook 加「啟動前查 tag」
- **T-M4 無金鑰失敗路徑真實觸發(10/04)**:撤 OpenAI 角色 → Job 3 秒 Failed、`run_meta` 含 401 PermissionDenied 與缺的 data action;恢復 → diag 五階段全綠;事實:認證失敗第一案即整輪中止(`uploaded=0`)、撤銷數分鐘內生效;踩坑:`$pid` 為 PowerShell 唯讀變數、diag 覆寫啟動必帶 `--image`(W18)
- **T-M5 成本(10/4 實帳,10/05 讀)**:≈NT$178(預估 175);Foundry Models 174(gpt-4o 輸出 token 佔 44%)、ACR 4.38、Logic Apps 0.02;Container Apps / Log Analytics / Storage 未出現在計量表(免費額度內)→ SPEC-t 「只在執行時計費」成立,**T-M1~T-M5 全過、SPEC-t 結案**;月累計 1,452.63 / 2,000
- **SPEC-v Content Safety 接入(10/05,程式側)**:方案 A 影子模式(被否決:進 `run_gate` 破壞確定性/可離線 regate;gpt-4o 自評違反 ADR-008)——`CSClient`(groundedness + 四類,key/Entra,重試,裁切)、`safety_check`(text = 遮罩後 reasoning + relevance;sources = 診斷書/收據/**引用條款原文**;例外不中斷)、`PipelineOutput.safety` 影子欄位(無 `--safety` 位元組不變)、eval `groundedness` 分群分布、卡片/摘要 `ungrounded` 欄、`tools/resafety` 離線補跑、Bicep `enableContentSafety`;1022 tests / 變異 102+5+6;假設 1(F0 支援)/2(中文誤報率)/3(角色)待桌機 `cs_smoke` 與 10 組 `resafety`
- **SPEC-v 假設 2 三輪量測 → 負結果結案(10/05)**:句界只認 ASCII `.`(程式切句修正)、推導事實入 sources(−0.12)、條款標籤 + 數字旁註(無效);中文理由 approve 中位數 0.58 → 0.46 → 0.42(門檻 0.15,結案線 0.35);英文理由 0.62~0.99 為最大因子(prompt 層);C066 既往症捏造句在加條款 source 後反而不再被判無據(API 無法分辨「條款定義 X」與「診斷書記載 X」)→ groundedness 否決為路由訊號與窄域後援;接入(F0 / Entra / Bicep / 影子欄位 / eval / 卡片 / resafety)保留;1030 tests / 變異 105+5+6
- **SPEC-v 第二段 → ADR-009 accepted(10/05)**:E1 `resafety --repeat 3` 10 案極差全 0(API 決定性,三輪差異非噪音);E2 QnA 逐主張事前判準三項全敗(C066 捏造 0/3 抓到、C024 合法拒付 3/3 被判捏造、乾淨案日數 12/12 誤報);E3-a Prompt Shields 三種注入文案只命中英文 1 種;E3-b zh 注入端到端判定/金額與基準相同、Gate 以 `treatment_summary` 低信心改派人工 → groundedness 不路由、Shields 不接主管線、Job 建議不帶 `--safety`;補件後 en/note 亦與基準相同(三變體皆 manual);Job 定案 `enableContentSafety=false`;1039 tests / 變異 107+5+6
- **E3-c 拒付案注入(10/05)**:判準事前提交後跑 C042(酒駕除外)/C024(既往症)× en/zh/note;6/6 未放行、判定翻轉 0、理由無注入措辭,C042 en/zh 因信心下降改派人工(更保守);合併 E3-b 共 9 變體放行 0 → ADR-009 第 3 點證據補齊
- **SPEC-w 合法值但非本案值(10/05)**:查 C070/C035 時以現行 Gate 重放 11 輪 114 件金額錯核准案次,3 件未被金額類檢核接住(v2 C054 放行、v8 C035/C060 僅靠引文旗標)——共同型態是填了合法值集合內但非本案的值;新增 `calc_surgery_grade_value`(POL-A 單段手術定額 / POL-C 第9條限額須符摘要最高等級)與 `calc_stay_days_uncovered`(多段住院日數合計須涵蓋全部段);重放誤旗標 0、接住 16、放行錯 1→0、只剩引文旗標的金額錯 2→0;C070 金額 11 輪全對,不處理;1058 tests / 變異 114+5+6;旗標 39 種
- **SPEC-x 可確定金額顧問欄位(10/05)**:部分核付三方案比較後選中間方案(三分類 + manual 案顧問欄位,無放行權);查證保險法施行細則第7條「已認定部分先行給付」→ 推翻 C064「POL-A 無部分給付機制」,C041/C064 真值補可確定金額 13,800 / 6,000;schema / finalize 強制清空 / 卡片 / eval / prompt 14a;實作中抓到 regate 轉草稿崩潰缺陷;1074 tests / 變異 120+5+6;桌機實測判準已事前提交
- **SPEC-x 桌機實測 → 撤回 prompt 規則(10/05)**:前兩輪因桌機 pull 被未追蹤截圖同名衝突中止而跑在舊程式(runbook W21);第三輪有效:C041/C064 模型原判 6/6 approve、可確定金額 6/6 空(歷史 35/35 approve,人工皆 Gate 改派)→ 方案 B 撤回規則 14a 並中性化 schema 描述,欄位/eval/真值保留;固定解法 26
- **v11 全量(10/06,`3cad367`,首次版本戳記確認)**:Gate 前 0.806(同 v10)、Gate 後 0.736(v10 0.792,−4 案,事前判準 ±3 未過)、放行 26 件全對、真值人工放行 0、SPEC-w 新旗標誤旗標 0、schema 新欄位 72/72 空;−4 逐案歸因全為模型輸出(5 件核准金額錯被 Gate 正確攔下、2 件拒付被判核准亦攔下),無程式變更造成的失分;10/5 實帳約 NT$103,月累計 NT$1,556
- **SPEC-y C073(10/06)**:查證示範條款扣除對象限實支實付型、新制不及日額型 → 維持「不扣」並加案(與 C072 同組文件,只改收據正本與他保型態,approve 78,000;分布 41/23/9、73 案);prompt 規則 9a 改以 `is_copy` 為唯一扣除觸發、正本不因他保轉人工;Gate 新增 `deduction_without_copy`(歷史 935 案次觸發 0);regate 缺件仍評分;桌機判準已事前提交
- **SPEC-y 桌機 v12(10/06)→ 撤回 prompt 規則 9a**:C073 ×3 扣除 0/3、2/3 核准 78,000(硬門檻過);但全量 C072 他保病房費扣在雜費桶放行 +10,000(歷次首件放行錯)、C071 退步 → 依事前判準撤回 9a,保留 Gate `deduction_without_copy` 與 C073 真值;發現 Gate 不檢查扣除落桶,探針 14 輪誤旗標 0、接住 3 → 候選 SPEC-z
- **SPEC-z 落桶檢核(10/06)**:Gate 新增 `deduction_bucket_mismatch`(收據影本時各條扣除須等於申請書對應桶他保金額);14 輪 1008 案次重放觸發 3 件皆金額錯 C072、誤旗標 0,v12 C072 放行錯消除;1101 tests / 變異 128+5+6;固定解法 27 前置條件成立,可重試規則 9a

## 2026-10-06 SPEC-y2 規則 9a 重試
- SPEC-z 補上落桶 Gate 後重試 9a:扣除僅限收據影本、桶 ↔ 條款對應明列、正本不扣不轉人工;Y4 三測試先紅後綠;gauntlet 1103 / 128+5+6 全過
- 桌機事前判準見 EVIDENCE 第三十四部分;硬判準不過即撤回 9a

## 2026-10-06 SPEC-y2 桌機 v13 + SPEC-aa
- v13(HEAD `f39b03b`):C073 ×3 扣除 0/3、78,000 2/3;C070–C073 全對;前 72 案 Gate 後 53(= v11);C047 放行 5,000(真值 2,500)— 與 9a 無關之 Gate 既有漏洞
- SPEC-aa 新旗標 `surgery_repeat_within_14d`(POL-B 第10條):15 輪重放放行錯 0、誤旗標 0;9a 保留;gauntlet 1112 / 136+5+6

## 2026-10-06 SPEC-ab 列舉條款引文整項照抄
- 查 C070/C035 引文 15 輪:Gate 判不符皆正確(模型拼接分項 / 「、」串接改寫),結案;規則 2 增補整項照抄、「…」隔開、禁拼接
- 與 v14 合併全量;事前判準見 EVIDENCE 第三十六部分;gauntlet 1120 / 136+5+6

## 2026-10-06 v14(SPEC-ab 撤回)+ SPEC-ac
- v14 C035 拼接 3/4 未改善、前 72 案 Gate 後 −6、C029 放行錯 → 依事前判準撤回規則 2 增補(prompt 回 `8ee50a9`)
- SPEC-ac `calc_surgery_grade_value` 延伸 POL-B 單一手術:16 輪重放放行錯 0、誤旗標 0、接住 7;gauntlet 1129 / 140+5+6

## 2026-10-06 v15(撤回後確認 + SPEC-aa/ac 首跑)
- Gate 後 0.726(前 72 案 52,v13 ±3 內)、Gate 前 0.7945(v13 −0.041,邊界未過)、放行 26 全對、金額 exact 1.0;新旗標 C027/C047 皆接住真錯;9a 定案保留

## 2026-10-06 Gate 矩陣盤點 + SPEC-ad
- 固定解法 28 首次全量套用:8 個缺口離線量測,僅 POL-C 雜費多填有實證(34 錯皆由 `receipt_item_unclassified` 接住、放行 0)→ 本輪不補
- SPEC-ad `deduction_unallocated`:影本 + 無法歸桶 → 人工(原僅 prompt 9a 守住);17 輪重放觸發 0、誤旗標 0;gauntlet 1134 / 145+5+6;Gate 旗標 42 種
- 逐案波動:近期多輪區間(前 72 案,v10–v15 不含 v14)Gate 前 47–51、Gate 後 52–57;Gate 改派模型正確答案 105 案次,其中 citation_quote_mismatch 67

## 2026-10-07 SPEC-ae 改善流程階段 1
- ①`waiting_period_prior_diagnosis`(三保單第24/21條)接住 C058/C049 29/29、誤旗標 0 ②放行抽樣稽核 10% ④引文 仟→千;③ issue_date 豁免致 C059 放行錯 8 → 撤回(固定解法 31)
- 17 輪重放放行錯 0、Gate 後正確 870→871;gauntlet 1150 / 155+5+6;Gate 旗標 43 種
- SPEC-af `calc_stay_cap_merged`(POL-C 第10條合併限額少付):17 輪觸發 11 全為 C059 真錯、誤旗標 0;gauntlet 1156 / 160+5+6;Gate 旗標 44 種
- SPEC-ag:90 日提醒改依附錄二 ICD(觸發 42→14)、issue_date 豁免重試成功;17 輪 Gate 後正確 871→880、放行錯 0;gauntlet 1189 / 166+5+6
- SPEC-ah(改善流程 ⑥,未部署):Event Grid → Queue → 事件 Job(`cloud_run --from-queue`,先刪後跑、案號白名單)+ Job 失敗告警 + 共用 Log Analytics;CI Bicep 0.30.23→0.36.1 + `tools/bicep_check.py` ARM 不變式;gauntlet 1242 / 172+5+6
- ⑦ Cosmos 去留:Cost analysis 10 月 `cos-claimcrew` <NT$0.01、程式零引用 → 移除 Bicep 模組(ADR-010,方案 A);實體待 ⑧ 拆除;附帶發現 eastus2 另有 `fdy-claimcrew`(tag `claimcrew-probe`)本月 NT$7.19,待使用者確認
- ⑧ 拆除 → 重建(10/08):拆除 27 分;重建遇告警空表(兩段部署)、eastus Container Apps 容量不足(移 eastus2)、di_smoke 預設舊模型三坑;DI 同 ID 重訓 8/8、Search 1.0、T1 事件觸發 1 分鐘、T2 告警 6 分鐘到;SPEC-ah 兩假設皆證實(EVIDENCE 44)
- SPEC-ai(10/08):`surgery_definition_deny` POL-B 拒付引第2條 → 人工;18 輪觸發 5 全為真值人工(C031×3、C007×2)、誤攔 0;放行錯口徑更正(含錯誤自動拒付)6→1(殘留 v2 C061);Gate 旗標 45 種;gauntlet 1248 / 177+5+6

## 2026-10-09 ~ 10-11 改善流程階段 5(⑩~⑫)+ v17 全量回歸
- ⑩ SPEC-aj 程式填引文:模型只指 segment、程式自條款庫填 quote,Gate 新增 `citation_segment_not_found`;第一次 13 案線上子集 C069 放行 +1,300 → 回退映像、補 `cap_group_clause` → 第二次子集放行錯 0(固定解法 35)
- ⑪ SPEC-ak 掃描化 / 雜訊 PDF 變體(13 案 × 輕 / 重):放行錯 0、DI 誤讀皆被低信心旗標接住;SPEC-al 補 `calc_ratio_inconsistent` 與報錯案輸出 manual_review + `pipeline_error`
- ⑫ SPEC-am 覆核閉環:摘要信卡片連結 → Logic App 表單 → 受控身分寫 `reviews-inbox/` → `tools/review_stats.py`;Gate 旗標 47 種;gauntlet 1362 / 225+5+6;映像 `d804ec3`
- v17 全量(run `20261011T0513-d804ec3`,EVIDENCE 48):J1–J6 全過,放行錯 0/0、Gate 後 0.726、金額 exact 1.0、危險錯 0;當日 NT$66.8

## 2026-10-12 ~ 10-19 Phase 3 收尾
- 10/12~13:SPEC 回填、工時口徑定案(commit 時戳估算,§15);Demo 錄影規劃(規劃文件已於公開前移除)
- 10/15:Demo 跑案 run `20261015T1029-d804ec3`(C004 Gate 改判拒付、C010 / C060 攔下模型算錯),覆核閉環 2/2;A1~A20 錄製
- 10/16:第二次全拆 15 分 09 秒(⑧ 為 27 分),3 個軟刪除帳戶 purge;Runbook §7 漂移清單 D1~D17;Demo 影片上傳 https://youtu.be/emzxXhkF_Tk
- 10/17:Cost analysis 全部計量 NT$0(B6),月累計停在 NT$2,189.19
- 10/19:ADR-009/010 英譯、ADR-008 補 Agent Service 使用邊界(E11);公開前掃描(文字檔無金鑰、6 張截圖遮蔽訂閱 ID / email)

## 收尾統計(2026-10-19)

| 項目 | 數字 | 來源 |
|---|---|---|
| 日曆期間 | 2026-08-30 ~ 10-19(有 commit 的日子 26 天) | `git log` |
| 工時 | 約 99 h(Phase 0 ≈15、Phase 1 ≈26、Phase 2 ≈58;Phase 3 未計)| SPEC §15 口徑:commit 間隔 ≤ 60 分為同段、每段 +30 分;下限 |
| Commits | 543 | `git rev-list --count HEAD` |
| 程式 | `app/` + `tools/` + `eval/` Python 10,402 行;Bicep 1,107 行 | `git ls-files` 行數 |
| 測試 | 1,362 tests(44 檔、7,940 行);手動變異 225 + 5 + 6 全殺 | `pytest --collect-only`;gauntlet `d804ec3` |
| 合成資料 | 73 案(approve 41 / deny 23 / manual 9),3 張保單,零 PHI | 真值凍結 |
| Gate | 47 種確定性旗標 | SPEC-am 結案 |
| 迭代 | SPEC-b ~ SPEC-am 共 37 份;撤回 / 否決的例子:獨立 Policy agent、Repair 放行權、groundedness、SPEC-ab、SPEC-x、9a 首次改寫、issue_date 首次豁免 | EVIDENCE 49 部分 |
| ADR | 10 份,全英文 | `docs/adr/` |
| 品質(v17) | 放行錯 0/0;Gate 後 0.726、Gate 前 0.808(判定口徑);放行金額 exact 1.0;危險錯 0;manual recall 1.0 | EVIDENCE 48 |
| 雲端費用 | 10 月累計 NT$2,189.19(預算 3,000);約 97% 為 Foundry Models;一輪 73 案全量約 NT$67;閒置約 NT$4–5 / 日;拆除後 NT$0 / 日 | Cost analysis |
| 拆建 | 拆除 27 分 → 15 分;重建一次完成(3 個部署坑記入 Runbook) | EVIDENCE 44 / 49 |
