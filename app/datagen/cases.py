"""手寫案件(C001–C010 首批 + C041–C072)規格與生成器(全合成資料,零 PHI)。

設計原則:
- 案件規格與真值為靜態宣告(非隨機),eval set 可凍結、可 diff
- 真值(decision / amount / clause_basis)為「提議值」,附推導式,
  經使用者抽驗核可後凍結(人類保留區:領域真值)
- 執行:python3 -m app.datagen.cases [--force] → 寫入 data/claims/C###/
  (claim.json=文件內容;truth.json=真值標註;--force 才覆蓋既有目錄)

住院日數慣例(2026-08-30 使用者定案):含入出院日(首尾皆計)。

涵蓋設計(SPEC §4):順利件×3、等待期未滿×1、既往症×1、文件缺漏×1、
收據與診斷不符×3(C007 POL-B 手術費無手術記載→第22條內容要求直接適用;C009 POL-A 病房費日數多於住院日數→目的性擴張;
C041 POL-C 處置費性質不明→第26條專條)、
同一次住院合併+日數上限×1、非健保身分+雜費負面項目×1;
未列表手術需人工件由擴量 C040 涵蓋(C009 於 2026-09-07 改案,理由見 truth derivation)。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

CLAIMS_DIR = Path(__file__).resolve().parents[2] / "data" / "claims"

# 每案結構:documents(三件套內容,缺件以 None 表示)+ truth(提議真值)
# 所有人名/證號/院名均為虛構合成值
CASES: list[dict[str, Any]] = [
    {
        "claim_id": "C001",
        "scenario": "順利件:單純疾病住院(POL-A)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "王大明",
                "diagnosis": "肺炎",
                "icd_code": "J18.9",
                "admissions": [
                    {"admission_date": "2026-03-02", "discharge_date": "2026-03-07", "icu_days": "0"},
                ],
                "treatment_summary": "抗生素治療,住院五日,病況穩定後出院",
                "issue_date": "2026-03-07",
                "hospital_name": "安心綜合醫院",
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "王大明",
                "receipt_date": "2026-03-07",
                "items": [
                    {"item_name": "病房費", "amount": "7500"},
                    {"item_name": "藥費", "amount": "3200"},
                    {"item_name": "檢驗費", "amount": "2800"},
                ],
                "total_amount": "13500",
            },
            "claim_application": {
                "applicant_name": "王大明",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-01-10",
                "claim_type": "住院醫療",
                "application_date": "2026-03-15",
                "claimed_amount": "18000",
            },
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "18000",
            "derivation": "住院 3/2~3/7 含入出院日共 6 日;日額 2000×6(第7條)+ 輔助 1000×6(第11條)= 18,000;等待期已滿(生效 2025-01-10)",
            "clause_basis": ["POL-A 第7條", "POL-A 第11條"],
        },
    },
    {
        "claim_id": "C002",
        "scenario": "順利件:疾病住院含手術(POL-A)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "林美華",
                "diagnosis": "急性膽囊炎",
                "icd_code": "K81.0",
                "admissions": [
                    {"admission_date": "2026-04-10", "discharge_date": "2026-04-14", "icu_days": "0"},
                ],
                "treatment_summary": "行腹腔鏡膽囊切除術,術後恢復良好",
                "issue_date": "2026-04-14",
                "hospital_name": "安心綜合醫院",
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "林美華",
                "receipt_date": "2026-04-14",
                "items": [
                    {"item_name": "病房費", "amount": "6000"},
                    {"item_name": "手術費", "amount": "28000"},
                    {"item_name": "麻醉費", "amount": "6500"},
                    {"item_name": "藥費", "amount": "4100"},
                ],
                "total_amount": "44600",
            },
            "claim_application": {
                "applicant_name": "林美華",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2024-08-01",
                "claim_type": "住院醫療",
                "application_date": "2026-04-20",
                "claimed_amount": "40000",
            },
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "40000",
            "derivation": "住院 4/10~4/14 含入出院日共 5 日;日額 2000×5 + 輔助 1000×5 = 15,000;膽囊切除術為附錄一第二級 25,000(第10條);合計 40,000",
            "clause_basis": ["POL-A 第7條", "POL-A 第10條", "POL-A 第11條", "POL-A 附錄一"],
        },
    },
    {
        "claim_id": "C003",
        "scenario": "順利件:實支實付逐項限額(POL-C)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "陳志強",
                "diagnosis": "急性闌尾炎",
                "icd_code": "K35.8",
                "admissions": [
                    {"admission_date": "2026-05-03", "discharge_date": "2026-05-06", "icu_days": "0"},
                ],
                "treatment_summary": "行腹腔鏡闌尾切除術,以全民健康保險身分住院",
                "issue_date": "2026-05-06",
                "hospital_name": "平安紀念醫院",
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "陳志強",
                "receipt_date": "2026-05-06",
                "items": [
                    {"item_name": "病房費", "amount": "7500"},
                    {"item_name": "手術費", "amount": "18000"},
                    {"item_name": "麻醉費", "amount": "4000"},
                    {"item_name": "藥費", "amount": "9800"},
                    {"item_name": "醫材費", "amount": "6200"},
                    {"item_name": "檢驗費", "amount": "2420"},
                ],
                "total_amount": "47920",
            },
            "claim_application": {
                "applicant_name": "陳志強",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-02-15",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-05-12",
                "claimed_amount": "45920",
            },
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "45920",
            "derivation": "住院含入出院日 4 日;病房費 min(7,500, 3,000×4)=7,500(第8條);手術費+麻醉費 22,000,闌尾切除為 POL-C 附錄一第一級,限額 80,000×25%=20,000(第9條)→給 20,000;雜費(藥+醫材+檢驗)18,420,未逾 60,000(第10條);合計 45,920",
            "clause_basis": ["POL-C 第8條", "POL-C 第9條", "POL-C 第10條", "POL-C 附錄一"],
        },
    },
    {
        "claim_id": "C004",
        "scenario": "等待期未滿件(POL-A 第22條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "張淑芬",
                "diagnosis": "急性腸胃炎",
                "icd_code": "K52.9",
                "admissions": [
                    {"admission_date": "2026-03-10", "discharge_date": "2026-03-12", "icu_days": "0"},
                ],
                "treatment_summary": "輸液及症狀治療後出院",
                "issue_date": "2026-03-12",
                "hospital_name": "安心綜合醫院",
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "張淑芬",
                "receipt_date": "2026-03-12",
                "items": [
                    {"item_name": "病房費", "amount": "3000"},
                    {"item_name": "藥費", "amount": "1800"},
                ],
                "total_amount": "4800",
            },
            "claim_application": {
                "applicant_name": "張淑芬",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2026-02-20",
                "claim_type": "住院醫療",
                "application_date": "2026-03-18",
                "claimed_amount": "9000",
            },
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "契約生效 2026-02-20,因疾病住院 2026-03-10,生效後僅 18 日,未滿 30 日疾病等待期(第22條)→ 不給付",
            "clause_basis": ["POL-A 第22條"],
        },
    },
    {
        "claim_id": "C005",
        "scenario": "既往症拒付件(POL-A 第18條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "李文雄",
                "diagnosis": "第二型糖尿病併發糖尿病足感染",
                "icd_code": "E11.621",
                "admissions": [
                    {"admission_date": "2026-06-01", "discharge_date": "2026-06-10", "icu_days": "0"},
                ],
                "treatment_summary": "清創及抗生素治療。病患有糖尿病病史五年,長期於本院新陳代謝科門診追蹤",
                "issue_date": "2026-06-10",
                "hospital_name": "平安紀念醫院",
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "李文雄",
                "receipt_date": "2026-06-10",
                "items": [
                    {"item_name": "病房費", "amount": "13500"},
                    {"item_name": "手術費", "amount": "12000"},
                    {"item_name": "藥費", "amount": "8600"},
                ],
                "total_amount": "34100",
            },
            "claim_application": {
                "applicant_name": "李文雄",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-09-01",
                "claim_type": "住院醫療",
                "application_date": "2026-06-15",
                "claimed_amount": "37000",
            },
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "診斷書載明糖尿病病史五年、長期門診追蹤,投保(2025-09-01)前二年內已接受診療,屬既往症;本次住院為糖尿病之併發症(第2條第五款、第18條)→ 不給付",
            "clause_basis": ["POL-A 第2條", "POL-A 第18條"],
        },
    },
    {
        "claim_id": "C006",
        "scenario": "文件缺漏件:無醫療收據(POL-A 第25條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "黃春嬌",
                "diagnosis": "左側股骨頸骨折",
                "icd_code": "S72.001",
                "admissions": [
                    {"admission_date": "2026-05-20", "discharge_date": "2026-05-29", "icu_days": "0"},
                ],
                "treatment_summary": "跌倒致骨折,行開放復位內固定術",
                "issue_date": "2026-05-29",
                "hospital_name": "安心綜合醫院",
            },
            "medical_receipt": None,
            "claim_application": {
                "applicant_name": "黃春嬌",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2024-11-20",
                "claim_type": "住院醫療",
                "application_date": "2026-06-02",
                "claimed_amount": "52000",
            },
        },
        "truth": {
            "decision": "manual_review",
            "approved_amount": "0",
            "derivation": "應備文件缺醫療費用收據正本(第25條第三款),應依第26條通知補正;Intake 階段即應中止並產出補件清單,不進入判定",
            "clause_basis": ["POL-A 第25條", "POL-A 第26條"],
        },
    },
    {
        "claim_id": "C007",
        "scenario": "收據與診斷不符件:收據有手術費、診斷書無手術記載(POL-B)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "吳建宏",
                "diagnosis": "上消化道出血",
                "icd_code": "K92.2",
                "admissions": [
                    {"admission_date": "2026-04-22", "discharge_date": "2026-04-26", "icu_days": "0"},
                ],
                "treatment_summary": "行胃鏡檢查確認出血點,予止血藥物治療後穩定",
                "issue_date": "2026-04-26",
                "hospital_name": "平安紀念醫院",
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "吳建宏",
                "receipt_date": "2026-04-26",
                "items": [
                    {"item_name": "病房費", "amount": "6000"},
                    {"item_name": "手術費", "amount": "15000"},
                    {"item_name": "藥費", "amount": "5200"},
                ],
                "total_amount": "26200",
            },
            "claim_application": {
                "applicant_name": "吳建宏",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2025-03-10",
                "claim_type": "手術醫療",
                "application_date": "2026-05-02",
                "claimed_amount": "19000",
            },
        },
        "truth": {
            "decision": "manual_review",
            "approved_amount": "0",
            "derivation": "收據列有手術費 15,000,已足以形成「曾接受手術」之表面證據;診斷書治療摘要僅載胃鏡檢查與止血藥物治療(胃鏡依第2條第三款屬診斷性處置,不視為手術),未載明手術名稱及手術日期,不符第22條第二款所定文件內容,依第23條通知補正。無論何者為真,出口相同:若確有手術 → 診斷書內容不合規,須補載手術名稱與日期;若無手術 → 收據列項有誤,須醫院更正收據明細;兩者皆須回頭向醫院查證 → 轉人工。與 C009(POL-A,無文件內容要求、須目的性擴張)對照:本案第22條第二款有內容要求,第23條直接適用,不必擴張(2026-09-07 依人工檢核修訂推導式,判定與條號不變)",
            "clause_basis": ["POL-B 第2條", "POL-B 第22條", "POL-B 第23條"],
        },
    },
    {
        "claim_id": "C008",
        "scenario": "同一次住院合併+日數上限件(POL-A 第12、13條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "劉金水",
                "diagnosis": "腦梗塞",
                "icd_code": "I63.9",
                "admissions": [
                    {"admission_date": "2026-01-05", "discharge_date": "2026-02-24", "icu_days": "0"},
                    {"admission_date": "2026-03-06", "discharge_date": "2026-04-20", "icu_days": "0"},
                ],
                "treatment_summary": "急性期治療後轉復健,2026-01-05 至 2026-02-24 住院共 51 日;出院後因同疾病於 2026-03-06 再次入院復健至 2026-04-20 共 46 日",
                "issue_date": "2026-04-20",
                "hospital_name": "安心綜合醫院",
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "劉金水",
                "receipt_date": "2026-04-20",
                "items": [
                    {"item_name": "病房費(兩次住院)", "amount": "142500"},
                    {"item_name": "復健治療費", "amount": "38000"},
                    {"item_name": "藥費", "amount": "21400"},
                ],
                "total_amount": "201900",
            },
            "claim_application": {
                "applicant_name": "劉金水",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-06-01",
                "claim_type": "住院醫療",
                "application_date": "2026-04-28",
                "claimed_amount": "291000",
            },
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "270000",
            "derivation": "第一次住院含入出院日 51 日,出院後 10 日(3/6)因同一疾病再住院 46 日,依第12條視為同一次住院,合併 97 日;依第13條同一次住院上限 90 日 → 認列 90 日;(日額 2000 + 輔助 1000)×90 = 270,000;腦梗塞屬附錄二特定疾病,生效 2025-06-01 至住院 2026-01-05 已逾 90 日等待期(第23條)",
            "clause_basis": ["POL-A 第7條", "POL-A 第11條", "POL-A 第12條", "POL-A 第13條", "POL-A 第23條"],
        },
    },
    {
        "claim_id": "C009",
        "scenario": "收據與診斷不符件:收據病房費日數多於診斷書住院日數(POL-A)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "許雅婷",
                "diagnosis": "急性腸胃炎",
                "icd_code": "K52.9",
                "admissions": [
                    {"admission_date": "2026-05-15", "discharge_date": "2026-05-16", "icu_days": "0"},
                ],
                "treatment_summary": "靜脈輸液與止瀉治療,症狀緩解後翌日出院",
                "issue_date": "2026-05-16",
                "hospital_name": "平安紀念醫院",
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "許雅婷",
                "receipt_date": "2026-05-16",
                "items": [
                    {"item_name": "病房費(5日)", "amount": "7500"},
                    {"item_name": "藥費", "amount": "2400"},
                    {"item_name": "檢驗費", "amount": "1800"},
                ],
                "total_amount": "11700",
            },
            "claim_application": {
                "applicant_name": "許雅婷",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-01-20",
                "claim_type": "住院醫療",
                "application_date": "2026-05-22",
                "claimed_amount": "15000",
            },
        },
        "truth": {
            "decision": "manual_review",
            "approved_amount": "0",
            "derivation": "診斷書載住院 5/15~5/16 含入出院日共 2 日,收據病房費卻以 5 日計列 7,500(若確為 5 日,出院日應為 5/19 而非 5/16,期間本身亦對不上),且申請金額 15,000 = 3,000×5 站在收據一側;第2條第二款「住院」以確實在醫院接受診療為要件,第7條與第11條均按「實際住院日數」給付,日數無從認定即算不出金額;實體上無任何拒付事由(等待期已滿、非附錄二、非既往症),故既不能核付亦不能拒付。文件形式上四項齊備,惟內容矛盾致無從認定給付要件,實質等同未提出可資認定之文件,目的性擴張適用第25條應備文件、第26條通知補正並轉人工;補正前文件未檢齊,第27條十五日給付期限尚未起算(2026-09-07 改案:原「未列表手術」情境與 C040 重複,改為第二個收據與診斷不符件以補強該類覆蓋;稽核 D29)",
            "clause_basis": ["POL-A 第2條", "POL-A 第7條", "POL-A 第11條", "POL-A 第25條", "POL-A 第26條", "POL-A 第27條"],
        },
    },
    {
        "claim_id": "C010",
        "scenario": "非健保身分+雜費負面項目件(POL-C 第10、12條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "蔡明哲",
                "diagnosis": "腰椎椎間盤突出",
                "icd_code": "M51.26",
                "admissions": [
                    {"admission_date": "2026-06-08", "discharge_date": "2026-06-12", "icu_days": "0"},
                ],
                "treatment_summary": "保守治療,自費身分入住非健保特約醫院",
                "issue_date": "2026-06-12",
                "hospital_name": "康泰醫院(非健保特約)",
            },
            "medical_receipt": {
                "hospital_name": "康泰醫院",
                "patient_name": "蔡明哲",
                "receipt_date": "2026-06-12",
                "items": [
                    {"item_name": "病房費", "amount": "16000"},
                    {"item_name": "藥費", "amount": "12000"},
                    {"item_name": "復健治療費", "amount": "13000"},
                    {"item_name": "診斷證明書費", "amount": "500"},
                    {"item_name": "掛號費", "amount": "150"},
                ],
                "total_amount": "41650",
            },
            "claim_application": {
                "applicant_name": "蔡明哲",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2024-12-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-06-18",
                "claimed_amount": "41650",
            },
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "26000",
            "derivation": "住院含入出院日 5 日;病房費 min(16,000, 3,000×5)=15,000(第8條);雜費:藥費 12,000 + 復健 13,000 = 25,000(第10條;診斷證明書費 500 與掛號費 150 屬負面列舉不計入);小計 40,000;非健保特約醫院就診依第12條 ×65% = 26,000",
            "clause_basis": ["POL-C 第8條", "POL-C 第10條", "POL-C 第12條"],
        },
    },
    {
        "claim_id": "C041",
        "scenario": "收據與診斷不符件:收據「處置費」性質不明,無法歸屬給付項目(POL-C 第26條專條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "黃淑芬",
                "diagnosis": "社區性肺炎",
                "icd_code": "J18.9",
                "admissions": [
                    {"admission_date": "2026-06-08", "discharge_date": "2026-06-11", "icu_days": "0"},
                ],
                "treatment_summary": "靜脈抗生素、氧氣與支持性治療,未施行手術,以全民健康保險身分住院",
                "issue_date": "2026-06-11",
                "hospital_name": "康寧聯合醫院",
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "黃淑芬",
                "receipt_date": "2026-06-11",
                "items": [
                    {"item_name": "病房費", "amount": "8000"},
                    {"item_name": "藥費", "amount": "3600"},
                    {"item_name": "處置費", "amount": "4000"},
                    {"item_name": "檢驗費", "amount": "2200"},
                ],
                "total_amount": "17800",
            },
            "claim_application": {
                "applicant_name": "黃淑芬",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-03-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-06-17",
                "claimed_amount": "17800",
            },
        },
        "truth": {
            "decision": "manual_review",
            "approved_amount": "0",
            "derivation": "病房費 min(8,000, 3,000×4)=8,000(第8條)、藥費 3,600 與檢驗費 2,200 屬第10條第一、三款列舉項目,三項合計 13,800 本身無爭議;健保身分第12條不觸發、等待期已滿。惟收據列「處置費」4,000 而無明細:診斷書明載未施行手術,第9條不在考慮;第10條但書所列不給付項目(證明書費、掛號費、看護費等)亦無一會以「處置費」列示;故唯一可能歸屬為第10條第六款「其他經醫師指示且與該次住院診療直接相關之醫療費用」,但一筆僅載「處置費」的金額無法證立「經醫師指示」與「直接相關」兩項要件——不是不知道放哪個桶,而是放進第六款所需的事實未被證明。第25條第三款本即要求收據正本含費用明細,本件欠缺,收據所載費用項目性質不明致無法判定應歸屬之給付項目,第26條專條觸發要件逐字該當:要求受益人提供醫院費用明細或補充說明、必要時洽詢原診治醫院;補不出則依第27條限期補正 → 轉人工。無爭議部分 13,800 依第7條「各項給付」可分結構,且保險法施行細則第7條要求就已認定部分先行給付;本系統維持三分類,判定仍為全案人工,13,800 以 SPEC-x 顧問欄位「可確定金額」呈現供人工給付(2026-10-05 修訂,判定與條號不變)。與 C007(POL-B 第22條第二款內容要求直接適用)、C009(POL-A 無內容要求須目的性擴張)對照:POL-C 有專條,專條專用(2026-09-07 新增;依人工檢核將處置費自 12,000 降為 4,000 以維持「單純性質不明」定位,並收緊為第六款單一活分支)",
            "clause_basis": ["POL-C 第8條", "POL-C 第10條", "POL-C 第25條", "POL-C 第26條", "POL-C 第27條"],
            "undisputed_amount": "13800",
            "disputed_items": ["處置費 4,000:收據項目性質不明,第10條第六款要件未證立,依第26條要求費用明細"],
        },
    },

    # --- 第三批(2026-09-08,人工檢核 11 v + C047 依意見換題):除外責任 / 但書陷阱 / POL-B 計算 ---
    {
        "claim_id": "C042",
        "scenario": "除外責任件:酒駕(POL-C 第17條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "楊志豪",
                "diagnosis": "右脛骨骨幹骨折",
                "icd_code": "S82.201",
                "admissions": [
                    {
                        "admission_date": "2026-04-12",
                        "discharge_date": "2026-04-16",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "騎乘機車自撞路樹送急診,右脛骨骨幹骨折,行骨折開放復位內固定術,以全民健康保險身分住院",
                "issue_date": "2026-04-16",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "楊志豪",
                "receipt_date": "2026-04-16",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "10000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "38000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "4800"
                    },
                    {
                        "item_name": "醫材費",
                        "amount": "12000"
                    }
                ],
                "total_amount": "70800"
            },
            "claim_application": {
                "applicant_name": "楊志豪",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-06-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-04-22",
                "claimed_amount": "70800"
            },
            "incident_statement": {
                "source": "police_record",
                "incident_date": "2026-04-12",
                "statement_text": "2026-04-12 當事人騎乘普通重型機車行經郊區道路自撞路樹,經警方到場實施吐氣酒精濃度測試為 0.55 mg/L,超過道路交通安全規則規定標準(0.15 mg/L),依法舉發;當事人由救護車送醫。"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "警方紀錄載明騎乘機車自撞時吐氣酒精濃度 0.55 mg/L,超過道路交通法令規定標準,所受傷害屬第17條除外事由;診斷書僅載醫療事實、未提酒駕,與警方紀錄互補而非矛盾;傷害雖不受第23條疾病等待期限制、骨折開放復位內固定術亦為附錄一第二級,但除外責任優先於給付要件 → 拒付",
            "clause_basis": [
                "POL-C 第2條",
                "POL-C 第17條"
            ]
        }
    },
    {
        "claim_id": "C043",
        "scenario": "除外責任件:美容整形+非健保身分陷阱(POL-C 第18條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "邱雅玲",
                "diagnosis": "眼瞼下垂(輕度,無視野缺損)",
                "icd_code": "H02.409",
                "admissions": [
                    {
                        "admission_date": "2026-05-20",
                        "discharge_date": "2026-05-21",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "應病患要求行雙側上眼瞼整形術(美容目的),無視野缺損,全程自費,非以全民健康保險身分住院",
                "issue_date": "2026-05-21",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "邱雅玲",
                "receipt_date": "2026-05-21",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "36000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "5000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "1200"
                    }
                ],
                "total_amount": "48200"
            },
            "claim_application": {
                "applicant_name": "邱雅玲",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-01-15",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-05-27",
                "claimed_amount": "48200"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "診斷書明載美容目的且無視野缺損,非因治療疾病或傷害所必要之診療,屬第18條除外;非健保身分之第12條 65% 折算僅適用於應給付之金額,對已除外之費用不生作用 → 拒付(若有視野缺損則屬治療必要,不在除外之列——本案差在此線)",
            "clause_basis": [
                "POL-C 第18條"
            ]
        }
    },
    {
        "claim_id": "C044",
        "scenario": "除外責任件:分娩(POL-C 第20條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "洪佩君",
                "diagnosis": "足月妊娠自然分娩",
                "icd_code": "O80",
                "admissions": [
                    {
                        "admission_date": "2026-06-02",
                        "discharge_date": "2026-06-05",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "自然產,母嬰均安,以全民健康保險身分住院",
                "issue_date": "2026-06-05",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "洪佩君",
                "receipt_date": "2026-06-05",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "12000"
                    },
                    {
                        "item_name": "接生費",
                        "amount": "15000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2400"
                    }
                ],
                "total_amount": "29400"
            },
            "claim_application": {
                "applicant_name": "洪佩君",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2024-11-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-06-11",
                "claimed_amount": "29400"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "分娩所致之住院屬第20條除外;逐一比對但書四款(子宮外孕、葡萄胎、前置胎盤、子癲前症及子癇症)皆不該當 → 拒付",
            "clause_basis": [
                "POL-C 第20條"
            ]
        }
    },
    {
        "claim_id": "C045",
        "scenario": "除外但書件:妊娠併發症子癲前症(POL-A 第19條但書,approve)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "簡怡君",
                "diagnosis": "子癲前症",
                "icd_code": "O14.90",
                "admissions": [
                    {
                        "admission_date": "2026-04-06",
                        "discharge_date": "2026-04-10",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "妊娠 32 週,血壓 165/105 mmHg 併蛋白尿,住院降壓與胎兒監測,未生產,病況穩定後出院",
                "issue_date": "2026-04-10",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "簡怡君",
                "receipt_date": "2026-04-10",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "7500"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "3800"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "4200"
                    }
                ],
                "total_amount": "15500"
            },
            "claim_application": {
                "applicant_name": "簡怡君",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-05-01",
                "claim_type": "住院醫療",
                "application_date": "2026-04-16",
                "claimed_amount": "15000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "15000",
            "derivation": "子癲前症為第19條但書第四款所列妊娠併發症,不受懷孕除外之限制;妊娠 32 週回推受孕約 2025-08,晚於生效日 2025-05-01,非既往症;等待期已滿、非附錄二;含入出院日 5 日;日額 2000×5(第7條)+ 輔助 1000×5(第11條)= 15,000",
            "clause_basis": [
                "POL-A 第7條",
                "POL-A 第11條",
                "POL-A 第19條"
            ]
        }
    },
    {
        "claim_id": "C046",
        "scenario": "除外責任件:牙科手術(POL-B 第18條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "曾建中",
                "diagnosis": "下顎阻生智齒(雙側)",
                "icd_code": "K01.1",
                "admissions": [
                    {
                        "admission_date": "2026-05-08",
                        "discharge_date": "2026-05-09",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "全身麻醉下行雙側下顎阻生齒拔除術,非外傷所致",
                "issue_date": "2026-05-09",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "曾建中",
                "receipt_date": "2026-05-09",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "4000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "22000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "8000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "1500"
                    }
                ],
                "total_amount": "35500"
            },
            "claim_application": {
                "applicant_name": "曾建中",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2025-02-10",
                "claim_type": "手術醫療",
                "application_date": "2026-05-15",
                "claimed_amount": "17000"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "阻生齒拔除屬牙科處置所接受之手術,依第18條除外;診斷書明載非外傷,但書不成立 → 拒付。術式雖未列附錄一,除外責任優先於給付要件,無須進入第11條未列表手術之核定",
            "clause_basis": [
                "POL-B 第18條"
            ]
        }
    },
    {
        "claim_id": "C047",
        "scenario": "金額計算件:同一部位十四日內再手術視為同一次(POL-B 第10條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "郭家瑜",
                "diagnosis": "內痔(第三度)併術後出血",
                "icd_code": "K64.2",
                "admissions": [
                    {
                        "admission_date": "2026-04-02",
                        "discharge_date": "2026-04-02",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-04-12",
                        "discharge_date": "2026-04-12",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "2026-04-02 門診行痔瘡切除術;2026-04-12 因同部位術後出血,門診再行痔瘡切除術止血。兩次皆為門診手術,未辦理住院",
                "issue_date": "2026-04-12",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "郭家瑜",
                "receipt_date": "2026-04-12",
                "items": [
                    {
                        "item_name": "手術費(04-02)",
                        "amount": "9000"
                    },
                    {
                        "item_name": "手術費(04-12)",
                        "amount": "7000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "1800"
                    }
                ],
                "total_amount": "17800"
            },
            "claim_application": {
                "applicant_name": "郭家瑜",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2025-01-05",
                "claim_type": "手術醫療",
                "application_date": "2026-04-18",
                "claimed_amount": "5000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "2500",
            "derivation": "痔瘡切除術為附錄一第一級 5,000;兩次皆為門診手術,依第8條按 50% 給付;第二次手術於前次手術後 10 日內就同一部位施行,依第10條視為同一次手術,以該等級手術保險金之一次給付為限 → 5,000×50% = 2,500;申請 5,000(兩次各計)超過部分不予給付",
            "clause_basis": [
                "POL-B 第2條",
                "POL-B 第8條",
                "POL-B 第10條",
                "POL-B 附錄一"
            ]
        }
    },
    {
        "claim_id": "C048",
        "scenario": "等待期例外件:傷害不受疾病等待期限制(POL-B 第20條第二項,approve)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "沈冠霖",
                "diagnosis": "左橈骨遠端骨折",
                "icd_code": "S52.501",
                "admissions": [
                    {
                        "admission_date": "2026-05-12",
                        "discharge_date": "2026-05-15",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "2026-05-12 騎乘自行車跌倒致傷(無飲酒),行骨折開放復位內固定術",
                "issue_date": "2026-05-15",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "沈冠霖",
                "receipt_date": "2026-05-15",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "8000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "32000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2600"
                    },
                    {
                        "item_name": "醫材費",
                        "amount": "15000"
                    }
                ],
                "total_amount": "63600"
            },
            "claim_application": {
                "applicant_name": "沈冠霖",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2026-05-01",
                "claim_type": "手術醫療",
                "application_date": "2026-05-21",
                "claimed_amount": "34000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "34000",
            "derivation": "生效 2026-05-01 至手術 2026-05-12 僅 11 日,惟本案為意外事故所致之傷害(第2條第七款),依第20條第二項不受疾病等待期限制;無飲酒、自行車無駕照要件,第14條不觸發;骨折開放復位內固定術為附錄一第三級 30,000(第7條)+ 慰問 1000×4 日(第12條)= 34,000",
            "clause_basis": [
                "POL-B 第2條",
                "POL-B 第7條",
                "POL-B 第12條",
                "POL-B 第20條",
                "POL-B 附錄一"
            ]
        }
    },
    {
        "claim_id": "C049",
        "scenario": "等待期內發病延續件(POL-A 第24條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "彭俊豪",
                "diagnosis": "膽結石併急性膽囊炎",
                "icd_code": "K80.00",
                "admissions": [
                    {
                        "admission_date": "2026-05-10",
                        "discharge_date": "2026-05-14",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "病患於 2026-03-20 本院門診超音波診斷膽結石,今因急性膽囊炎入院行膽囊切除術",
                "issue_date": "2026-05-14",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "彭俊豪",
                "receipt_date": "2026-05-14",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "7500"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "26000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "5000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "4100"
                    }
                ],
                "total_amount": "42600"
            },
            "claim_application": {
                "applicant_name": "彭俊豪",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2026-03-01",
                "claim_type": "住院醫療",
                "application_date": "2026-05-20",
                "claimed_amount": "40000"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "住院 2026-05-10 距生效 2026-03-01 已 70 日,逾第22條 30 日等待期;惟診斷書載明膽結石於 2026-03-20(生效後第 19 日,等待期內)已經診斷,急性膽囊炎為其併發症,依第24條該疾病及其併發症於等待期屆滿後之住院診療仍不負給付責任 → 拒付。僅以住院日減生效日判定等待期即為錯",
            "clause_basis": [
                "POL-A 第2條",
                "POL-A 第22條",
                "POL-A 第24條"
            ]
        }
    },
    {
        "claim_id": "C050",
        "scenario": "除外責任件:無照駕駛(POL-A 第16條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "余雅慧",
                "diagnosis": "右鎖骨骨折",
                "icd_code": "S42.001",
                "admissions": [
                    {
                        "admission_date": "2026-06-18",
                        "discharge_date": "2026-06-21",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "騎乘機車自摔致右鎖骨骨折,行骨折開放復位內固定術",
                "issue_date": "2026-06-21",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "余雅慧",
                "receipt_date": "2026-06-21",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "30000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "5000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2200"
                    }
                ],
                "total_amount": "43200"
            },
            "claim_application": {
                "applicant_name": "余雅慧",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-04-01",
                "claim_type": "住院醫療",
                "application_date": "2026-06-27",
                "claimed_amount": "37000"
            },
            "incident_statement": {
                "source": "police_record",
                "incident_date": "2026-06-18",
                "statement_text": "2026-06-18 當事人騎乘普通重型機車自行摔倒受傷,經警方到場查證,當事人未領有任何駕駛執照,依法舉發無照駕駛;當事人送醫治療。"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "警方紀錄載明當事人未領有駕駛執照(無照駕駛),所致之傷害屬第16條除外事由 → 拒付;診斷書僅載醫療事實,與警方紀錄互補而非矛盾;傷害不受第22條等待期限制、骨折開放復位內固定術為附錄一第二級,均不影響除外責任之優先適用",
            "clause_basis": [
                "POL-A 第16條"
            ]
        }
    },
    {
        "claim_id": "C051",
        "scenario": "金額計算件:同次麻醉多項手術(POL-B 第9條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "傅志偉",
                "diagnosis": "膽結石併腹股溝疝氣",
                "icd_code": "K80.20;K40.90",
                "admissions": [
                    {
                        "admission_date": "2026-03-03",
                        "discharge_date": "2026-03-07",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "同一次全身麻醉下行膽囊切除術併疝氣修補術",
                "issue_date": "2026-03-07",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "傅志偉",
                "receipt_date": "2026-03-07",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "10000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "48000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "9000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "3600"
                    },
                    {
                        "item_name": "醫材費",
                        "amount": "14000"
                    }
                ],
                "total_amount": "84600"
            },
            "claim_application": {
                "applicant_name": "傅志偉",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2024-12-01",
                "claim_type": "手術醫療",
                "application_date": "2026-03-13",
                "claimed_amount": "50000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "42500",
            "derivation": "同次麻醉接受二項手術,依第9條給付金額最高之一項全額、次高之一項 50%:膽囊切除術附錄一第三級 30,000 + 疝氣修補術第二級 15,000×50% = 7,500(第7條);慰問 1000×5 日(第12條);合計 42,500;申請 50,000 超過認列部分不予給付",
            "clause_basis": [
                "POL-B 第7條",
                "POL-B 第9條",
                "POL-B 第12條",
                "POL-B 附錄一"
            ]
        }
    },
    {
        "claim_id": "C052",
        "scenario": "既往症拒付件(POL-B 第16條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "詹美惠",
                "diagnosis": "子宮肌瘤",
                "icd_code": "D25.9",
                "admissions": [
                    {
                        "admission_date": "2026-04-14",
                        "discharge_date": "2026-04-17",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "病患自 2024-08 起於本院婦產科追蹤子宮肌瘤,近期經血過多致貧血,行子宮肌瘤切除術",
                "issue_date": "2026-04-17",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "詹美惠",
                "receipt_date": "2026-04-17",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "8000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "40000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "7000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "3000"
                    }
                ],
                "total_amount": "58000"
            },
            "claim_application": {
                "applicant_name": "詹美惠",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2025-10-01",
                "claim_type": "手術醫療",
                "application_date": "2026-04-23",
                "claimed_amount": "34000"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "子宮肌瘤自 2024-08 起追蹤,在投保(2025-10-01)前二年內已接受醫師診療,屬第2條第八款既往症;依第16條就既往症所接受之手術不負給付責任 → 拒付(子宮肌瘤切除術為附錄一第三級 30,000,其給付要件無須進入)",
            "clause_basis": [
                "POL-B 第2條",
                "POL-B 第16條"
            ]
        }
    },
    {
        "claim_id": "C053",
        "scenario": "除外但書件:牙科處置之意外例外(POL-A 第20條但書,approve)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "龔文彬",
                "diagnosis": "下顎骨骨折",
                "icd_code": "S02.609",
                "admissions": [
                    {
                        "admission_date": "2026-06-10",
                        "discharge_date": "2026-06-12",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "2026-06-10 遭遇車禍致下顎骨骨折(持有效駕照、無飲酒),行骨折開放復位內固定術,合併拔除二顆斷裂牙齒",
                "issue_date": "2026-06-12",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "龔文彬",
                "receipt_date": "2026-06-12",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "4500"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "35000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2300"
                    }
                ],
                "total_amount": "47800"
            },
            "claim_application": {
                "applicant_name": "龔文彬",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-03-01",
                "claim_type": "住院醫療",
                "application_date": "2026-06-18",
                "claimed_amount": "34000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "34000",
            "derivation": "本案涉及拔牙,惟係遭受意外傷害事故所致,依第20條但書不在牙科除外之列;第16條不觸發(持有效駕照、無飲酒);含入出院日 3 日;日額 2000×3(第7條)+ 輔助 1000×3(第11條)+ 骨折開放復位內固定術附錄一第二級 25,000(第10條)= 34,000。再推一層:拔牙未列附錄一,且第10條「同一次住院二項以上手術以給付金額最高之一項為限」,故即使但書不適用,金額亦不受影響",
            "clause_basis": [
                "POL-A 第7條",
                "POL-A 第10條",
                "POL-A 第11條",
                "POL-A 第20條",
                "POL-A 附錄一"
            ]
        }
    },

    # --- 第四批(2026-09-08,人工檢核 5 v):計算型條文與 B/C 等待期 ---
    {
        "claim_id": "C054",
        "scenario": "金額計算件:同一次住院多項手術以最高一項為限(POL-A 第10條;C051 鏡像)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "傅志偉",
                "diagnosis": "膽結石併腹股溝疝氣",
                "icd_code": "K80.20;K40.90",
                "admissions": [
                    {
                        "admission_date": "2026-03-03",
                        "discharge_date": "2026-03-07",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "同一次全身麻醉下行膽囊切除術併疝氣修補術",
                "issue_date": "2026-03-07",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "傅志偉",
                "receipt_date": "2026-03-07",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "10000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "48000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "9000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "3600"
                    },
                    {
                        "item_name": "醫材費",
                        "amount": "14000"
                    }
                ],
                "total_amount": "84600"
            },
            "claim_application": {
                "applicant_name": "傅志偉",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2024-12-01",
                "claim_type": "住院醫療",
                "application_date": "2026-03-13",
                "claimed_amount": "50000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "40000",
            "derivation": "含入出院日 5 日;日額 2000×5(第7條)+ 輔助 1000×5(第11條)= 15,000;膽囊切除術附錄一第二級 25,000、疝氣修補術第一級 10,000,依第10條「同一次住院接受二項以上手術者,以給付金額最高之一項為限」僅給 25,000(POL-B 第9條為次高 50%,POL-A 次高不給);合計 40,000;申請 50,000 超過部分不予給付。與 C051 為同一病人、同一組手術、同一收據,唯一變數是保單(40,000 vs 42,500)",
            "clause_basis": [
                "POL-A 第7條",
                "POL-A 第10條",
                "POL-A 第11條",
                "POL-A 附錄一"
            ]
        }
    },
    {
        "claim_id": "C055",
        "scenario": "金額計算件:十四日內再住院雜費限額合併(POL-C 第13條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "施佳玲",
                "diagnosis": "左下肢蜂窩性組織炎(復發)",
                "icd_code": "L03.116",
                "admissions": [
                    {
                        "admission_date": "2026-06-01",
                        "discharge_date": "2026-06-05",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-06-15",
                        "discharge_date": "2026-06-18",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "第 1 次住院靜脈抗生素治療(含自費抗生素)後出院;出院後 10 日因同一疾病復發再入院,續以自費抗生素治療;以全民健康保險身分住院",
                "issue_date": "2026-06-18",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "施佳玲",
                "receipt_date": "2026-06-18",
                "items": [
                    {
                        "item_name": "病房費(第1次)",
                        "amount": "12000"
                    },
                    {
                        "item_name": "藥費(第1次)",
                        "amount": "30000"
                    },
                    {
                        "item_name": "檢驗費(第1次)",
                        "amount": "15000"
                    },
                    {
                        "item_name": "病房費(第2次)",
                        "amount": "10000"
                    },
                    {
                        "item_name": "藥費(第2次)",
                        "amount": "18000"
                    },
                    {
                        "item_name": "檢驗費(第2次)",
                        "amount": "7000"
                    }
                ],
                "total_amount": "92000"
            },
            "claim_application": {
                "applicant_name": "施佳玲",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-02-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-06-24",
                "claimed_amount": "92000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "82000",
            "derivation": "第 2 次住院於出院後 10 日內因同一疾病再住院,依第13條視為同一次住院,第10條雜費限額合併計算:雜費(藥費+檢驗費)45,000 + 25,000 = 70,000 → min(70,000, 60,000)= 60,000(第 2 次僅餘 15,000 可給);病房費為逐日限額、各段獨立:min(12,000, 3,000×5)= 12,000、min(10,000, 3,000×4)= 10,000(第8條);合計 82,000。若誤將兩段各套 60,000 會多給 10,000",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第13條"
            ]
        }
    },
    {
        "claim_id": "C056",
        "scenario": "等待期未滿件:疾病手術(POL-B 第20條;與 C048 對照)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "陸冠宇",
                "diagnosis": "急性闌尾炎",
                "icd_code": "K35.8",
                "admissions": [
                    {
                        "admission_date": "2026-04-20",
                        "discharge_date": "2026-04-22",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "急診確診急性闌尾炎,當日行闌尾切除術",
                "issue_date": "2026-04-22",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "陸冠宇",
                "receipt_date": "2026-04-22",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "20000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "4000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "1800"
                    }
                ],
                "total_amount": "31800"
            },
            "claim_application": {
                "applicant_name": "陸冠宇",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2026-04-01",
                "claim_type": "手術醫療",
                "application_date": "2026-04-28",
                "claimed_amount": "18000"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "生效 2026-04-01 至接受手術 2026-04-20 僅 19 日(第20條以「接受手術」日起算,非住院日),屬疾病所致,依第20條第一項 30 日等待期內不負給付責任 → 拒付(闌尾切除術第二級 15,000 + 慰問 3,000 之給付要件無須進入)。與 C048 對照:生效日相近、一疾病一傷害、結論相反",
            "clause_basis": [
                "POL-B 第2條",
                "POL-B 第20條"
            ]
        }
    },
    {
        "claim_id": "C057",
        "scenario": "等待期未滿件:疾病住院(POL-C 第23條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "羅欣怡",
                "diagnosis": "急性腸胃炎",
                "icd_code": "K52.9",
                "admissions": [
                    {
                        "admission_date": "2026-05-25",
                        "discharge_date": "2026-05-27",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "靜脈輸液與止瀉治療,以全民健康保險身分住院",
                "issue_date": "2026-05-27",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "羅欣怡",
                "receipt_date": "2026-05-27",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2100"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "1600"
                    }
                ],
                "total_amount": "9700"
            },
            "claim_application": {
                "applicant_name": "羅欣怡",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2026-05-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-06-02",
                "claimed_amount": "9700"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "生效 2026-05-01 至住院 2026-05-25 僅 24 日,屬疾病住院,依第23條第一項 30 日等待期內不負給付責任 → 拒付;各項金額雖皆在第8、10條限額內,等待期為前置要件,不得先算金額再回頭查",
            "clause_basis": [
                "POL-C 第2條",
                "POL-C 第23條"
            ]
        }
    },
    {
        "claim_id": "C058",
        "scenario": "等待期內發病延續件(POL-C 第24條;C049 鏡像)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "翁明志",
                "diagnosis": "第 2 型糖尿病足部感染",
                "icd_code": "E11.621",
                "admissions": [
                    {
                        "admission_date": "2026-07-01",
                        "discharge_date": "2026-07-06",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "病患於 2026-05-10 本院新陳代謝科門診首次診斷第 2 型糖尿病,今因左足足部感染入院行清創與靜脈抗生素治療;以全民健康保險身分住院",
                "issue_date": "2026-07-06",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "翁明志",
                "receipt_date": "2026-07-06",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "15000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "9800"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "4600"
                    },
                    {
                        "item_name": "處置費(清創)",
                        "amount": "6000"
                    }
                ],
                "total_amount": "35400"
            },
            "claim_application": {
                "applicant_name": "翁明志",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2026-04-25",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-07-12",
                "claimed_amount": "35400"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "住院 2026-07-01 距生效 2026-04-25 已 67 日、逾第23條 30 日等待期;惟糖尿病於 2026-05-10(生效後第 15 日,等待期內)已經診斷,E11.621 即為糖尿病之足部併發症,依第24條該疾病及其併發症於等待期屆滿後之住院診療仍不負給付責任 → 拒付。本案不適用附錄二(POL-C 無特定疾病表,糖尿病 90 日延長等待期為 POL-A 第23條之規則),拒付依據為第24條而非第23條;若套 POL-A 規則得出「67 日 < 90 日 → 第23條拒付」,結論相同但條號錯",
            "clause_basis": [
                "POL-C 第2條",
                "POL-C 第23條",
                "POL-C 第24條"
            ]
        }
    },

    # --- 第五批(2026-09-08,人工檢核 7 v):反例組 / 年度 180 日 / 犯罪行為 / POL-A 傷害例外、未列表手術 / POL-B 診所 ---
    {
        "claim_id": "C059",
        "scenario": "反例件:出院後第 15 日再住院不視為同一次,雜費限額不合併(POL-C 第13條;C055 對照)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "柯宜庭",
                "diagnosis": "急性腎盂腎炎(復發)",
                "icd_code": "N10",
                "admissions": [
                    {
                        "admission_date": "2026-07-01",
                        "discharge_date": "2026-07-05",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-07-20",
                        "discharge_date": "2026-07-23",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "第 1 次住院靜脈抗生素(含自費抗生素)治療後出院;出院後第 15 日因同一疾病復發再入院,續以自費抗生素治療;以全民健康保險身分住院",
                "issue_date": "2026-07-23",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "柯宜庭",
                "receipt_date": "2026-07-23",
                "items": [
                    {
                        "item_name": "病房費(第1次)",
                        "amount": "12000"
                    },
                    {
                        "item_name": "藥費(第1次)",
                        "amount": "38000"
                    },
                    {
                        "item_name": "檢驗費(第1次)",
                        "amount": "12000"
                    },
                    {
                        "item_name": "病房費(第2次)",
                        "amount": "10000"
                    },
                    {
                        "item_name": "藥費(第2次)",
                        "amount": "22000"
                    },
                    {
                        "item_name": "檢驗費(第2次)",
                        "amount": "8000"
                    }
                ],
                "total_amount": "102000"
            },
            "claim_application": {
                "applicant_name": "柯宜庭",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-03-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-07-29",
                "claimed_amount": "102000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "102000",
            "derivation": "「出院後十四日內」起算約定:出院日不計,翌日起算第 1 日(SPEC §14 2026-09-08)。第 1 次 07-05 出院,07-20 再住院為第 15 日,逾第13條十四日內,不視為同一次住院,雜費限額不合併:第 1 次雜費 min(50,000, 60,000)= 50,000、第 2 次 min(30,000, 60,000)= 30,000(第10條);病房費 min(12,000, 3,000×5)= 12,000、min(10,000, 3,000×4)= 10,000(第8條);合計 102,000。若誤合併(80,000 → 截 60,000)會少給 20,000。與 C055(第 10 日,合併)成對照",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第13條"
            ]
        }
    },
    {
        "claim_id": "C060",
        "scenario": "反例件:出院後第 15 日再住院不視為同一次,日數各段獨立(POL-A 第12條;C008 對照)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "杜嘉玲",
                "diagnosis": "腦梗塞",
                "icd_code": "I63.9",
                "admissions": [
                    {
                        "admission_date": "2026-01-05",
                        "discharge_date": "2026-03-05",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-03-20",
                        "discharge_date": "2026-05-03",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "急性期治療後轉復健,病況穩定出院;出院後第 15 日因同疾病再入院復健",
                "issue_date": "2026-05-03",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "杜嘉玲",
                "receipt_date": "2026-05-03",
                "items": [
                    {
                        "item_name": "病房費(第1次)",
                        "amount": "90000"
                    },
                    {
                        "item_name": "復健治療費(第1次)",
                        "amount": "30000"
                    },
                    {
                        "item_name": "藥費(第1次)",
                        "amount": "15000"
                    },
                    {
                        "item_name": "病房費(第2次)",
                        "amount": "67500"
                    },
                    {
                        "item_name": "復健治療費(第2次)",
                        "amount": "25000"
                    },
                    {
                        "item_name": "藥費(第2次)",
                        "amount": "10000"
                    }
                ],
                "total_amount": "237500"
            },
            "claim_application": {
                "applicant_name": "杜嘉玲",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2024-09-01",
                "claim_type": "住院醫療",
                "application_date": "2026-05-09",
                "claimed_amount": "315000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "315000",
            "derivation": "「出院後十四日內」起算約定:出院日不計,翌日起算第 1 日。第 1 次 03-05 出院,03-20 再住院為第 15 日,逾第12條十四日內,不視為同一次住院,日數不合併、各段各自適用第13條 90 日上限:60 日與 45 日皆未逾;(日額 2000 + 輔助 1000)×(60 + 45)= 315,000;年度累計 105 日未逾 180 日;腦梗塞屬附錄二,生效 2024-09-01 至住院已逾 90 日(第23條)。若誤合併為 105 日 → 截 90 日 = 270,000,少給 45,000。C008 的鏡像(第 10 日合併→截 90),同一疾病、同一機制,只差 1 日",
            "clause_basis": [
                "POL-A 第7條",
                "POL-A 第11條",
                "POL-A 第12條",
                "POL-A 第13條",
                "POL-A 第23條"
            ]
        }
    },
    {
        "claim_id": "C061",
        "scenario": "金額計算件:同一保單年度累計 180 日上限(POL-A 第13條後段;三段不同疾病)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "范麗雲",
                "diagnosis": "重症肺炎併呼吸衰竭;右髖部骨折;腰椎壓迫性骨折",
                "icd_code": "J96.00;S72.001;S32.000",
                "admissions": [
                    {
                        "admission_date": "2025-10-15",
                        "discharge_date": "2025-12-23",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-02-01",
                        "discharge_date": "2026-04-01",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-06-01",
                        "discharge_date": "2026-08-04",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "第 1 次:重症肺炎併呼吸衰竭,呼吸照護與長期復健;第 2 次:在家跌倒致右髖部骨折,高齡採保守治療與復健;第 3 次:再次跌倒致腰椎壓迫性骨折,保守治療與復健;三次住院均無手術",
                "issue_date": "2026-08-04",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "范麗雲",
                "receipt_date": "2026-08-04",
                "items": [
                    {
                        "item_name": "病房費(第1次)",
                        "amount": "105000"
                    },
                    {
                        "item_name": "藥費(第1次)",
                        "amount": "42000"
                    },
                    {
                        "item_name": "病房費(第2次)",
                        "amount": "90000"
                    },
                    {
                        "item_name": "復健治療費(第2次)",
                        "amount": "36000"
                    },
                    {
                        "item_name": "病房費(第3次)",
                        "amount": "97500"
                    },
                    {
                        "item_name": "復健治療費(第3次)",
                        "amount": "39000"
                    }
                ],
                "total_amount": "409500"
            },
            "claim_application": {
                "applicant_name": "范麗雲",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-09-01",
                "claim_type": "住院醫療",
                "application_date": "2026-08-10",
                "claimed_amount": "585000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "540000",
            "derivation": "三段住院分屬不同疾病/傷害,第12條不合併,各段皆未逾第13條前段「同一次住院 90 日」;惟三段皆在同一保單年度(2025-09-01~2026-08-31)內,累計 70 + 60 + 65 = 195 日,逾第13條後段「同一保單年度累計 180 日」,第 3 段僅認列 50 日;(日額 2000 + 輔助 1000)×180 = 540,000;申請 585,000(195 日)超過部分不予給付;第 1 段住院距生效 44 日已逾 30 日等待期,三診斷皆非附錄二;第 2、3 段為跌倒所致之傷害,不受等待期限制。兩段各 ≤90 日時年度上限永不觸發,本案以三段設計。本案亦揭露 Gate 上界須按段數參數化(見 SPEC §14 2026-09-08)",
            "clause_basis": [
                "POL-A 第7條",
                "POL-A 第11條",
                "POL-A 第12條",
                "POL-A 第13條"
            ]
        }
    },
    {
        "claim_id": "C062",
        "scenario": "除外責任件:犯罪行為所致傷害(POL-B 第13條)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "柳承恩",
                "diagnosis": "左跟骨骨折",
                "icd_code": "S92.001",
                "admissions": [
                    {
                        "admission_date": "2026-05-03",
                        "discharge_date": "2026-05-06",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "自高處墜落致左跟骨骨折,行骨折開放復位內固定術",
                "issue_date": "2026-05-06",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "柳承恩",
                "receipt_date": "2026-05-06",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "8000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "30000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2500"
                    }
                ],
                "total_amount": "46500"
            },
            "claim_application": {
                "applicant_name": "柳承恩",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2025-01-10",
                "claim_type": "手術醫療",
                "application_date": "2026-05-12",
                "claimed_amount": "34000"
            },
            "incident_statement": {
                "source": "police_record",
                "incident_date": "2026-05-03",
                "statement_text": "2026-05-03 當事人侵入他人住宅行竊,遭屋主發現後自二樓陽台跳下逃逸致傷,經警方到場逮捕並移送偵辦;當事人送醫治療。"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "警方紀錄載明傷害係於犯罪行為(侵入住宅行竊、逃逸時跳樓)過程中所致,屬第13條「因犯罪行為所致之傷害」除外事由 → 拒付;診斷書僅載醫療事實(自高處墜落),與警方紀錄互補而非矛盾;骨折開放復位內固定術為附錄一第三級、傷害不受等待期限制,均不影響除外之優先適用。非醫療事實由第四類文件(警方紀錄)承載,診斷書只載醫療事實(2026-09-08 缺口 1 定案)",
            "clause_basis": [
                "POL-B 第13條"
            ]
        }
    },
    {
        "claim_id": "C063",
        "scenario": "等待期例外件:傷害不受疾病等待期限制(POL-A 第22條第二項;C048 的 A 鏡像)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "江承翰",
                "diagnosis": "左尺骨骨折",
                "icd_code": "S52.201",
                "admissions": [
                    {
                        "admission_date": "2026-06-09",
                        "discharge_date": "2026-06-12",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "2026-06-09 騎乘機車遭汽車追撞致傷(持有效駕照、無飲酒),行骨折開放復位內固定術",
                "issue_date": "2026-06-12",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "江承翰",
                "receipt_date": "2026-06-12",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "8000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "30000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "5000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "2000"
                    },
                    {
                        "item_name": "醫材費",
                        "amount": "12000"
                    }
                ],
                "total_amount": "57000"
            },
            "claim_application": {
                "applicant_name": "江承翰",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2026-06-01",
                "claim_type": "住院醫療",
                "application_date": "2026-06-18",
                "claimed_amount": "37000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "37000",
            "derivation": "生效 2026-06-01 至住院 2026-06-09 僅 8 日,惟本案為意外事故所致之傷害(第2條第四款),依第22條第二項不受疾病等待期限制;第16條不觸發(持有效駕照、無飲酒);含入出院日 4 日;日額 2000×4(第7條)+ 輔助 1000×4(第11條)+ 骨折開放復位內固定術附錄一第二級 25,000(第10條)= 37,000。POL-A 首件靠傷害例外核付之案(C042/C050 提及第22條第二項但皆為除外拒付)",
            "clause_basis": [
                "POL-A 第2條",
                "POL-A 第7條",
                "POL-A 第10條",
                "POL-A 第11條",
                "POL-A 第22條",
                "POL-A 附錄一"
            ]
        }
    },
    {
        "claim_id": "C064",
        "scenario": "未列表手術需人工件(POL-A 第10條末句;C009 舊題的 POL-A 版)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "歐陽靜",
                "diagnosis": "甲狀腺結節",
                "icd_code": "E04.1",
                "admissions": [
                    {
                        "admission_date": "2026-05-15",
                        "discharge_date": "2026-05-16",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "行甲狀腺結節射頻消融術(經皮微創),術後翌日出院",
                "issue_date": "2026-05-16",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "歐陽靜",
                "receipt_date": "2026-05-16",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "3000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "45000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "3000"
                    }
                ],
                "total_amount": "51000"
            },
            "claim_application": {
                "applicant_name": "歐陽靜",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2025-01-20",
                "claim_type": "住院醫療",
                "application_date": "2026-05-22",
                "claimed_amount": "31000"
            }
        },
        "truth": {
            "decision": "manual_review",
            "approved_amount": "0",
            "derivation": "日額 2000×2 + 輔助 1000×2 = 6,000 本身確定;惟射頻消融未列附錄一,依第10條末句「手術項目未列於附錄一者,由本公司參照該表中程度相當之手術等級核定之」屬保險公司裁量;且 POL-A 第2條無「手術」定義(POL-B 第2條第三款有,不得借用),射頻消融是否構成手術亦無條文可據 → 等級與定性均屬人工裁量;POL-A 契約無部分給付明文,惟保險法施行細則第7條要求就已認定部分先行給付;本系統維持三分類,判定仍為全案人工,6,000 以 SPEC-x 顧問欄位「可確定金額」呈現供人工給付(2026-10-05 修訂:原推導認定本保單不得部分給付,未考量施行細則第7條;判定與條號不變)。申請 31,000 = 6,000 + 第二級 25,000,申請人係比照甲狀腺部分切除術",
            "clause_basis": [
                "POL-A 第7條",
                "POL-A 第10條",
                "POL-A 第11條",
                "POL-A 附錄一"
            ],
            "undisputed_amount": "6000",
            "disputed_items": ["手術保險金:射頻消融未列附錄一,是否構成手術及比照等級屬第10條末句公司裁量"]
        }
    },
    {
        "claim_id": "C065",
        "scenario": "定義層級件:診所施行之門診手術(POL-B 第2條第二、五款,approve)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "戴俊宏",
                "diagnosis": "背部脂肪瘤",
                "icd_code": "D17.1",
                "admissions": [
                    {
                        "admission_date": "2026-04-08",
                        "discharge_date": "2026-04-08",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "背部脂肪瘤近期增大伴疼痛與壓迫不適,於本診所門診局部麻醉下行脂肪瘤切除術,未住院",
                "issue_date": "2026-04-08",
                "hospital_name": "仁心皮膚科診所"
            },
            "medical_receipt": {
                "hospital_name": "仁心皮膚科診所",
                "patient_name": "戴俊宏",
                "receipt_date": "2026-04-08",
                "items": [
                    {
                        "item_name": "手術費",
                        "amount": "6000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "500"
                    }
                ],
                "total_amount": "6500"
            },
            "claim_application": {
                "applicant_name": "戴俊宏",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2025-06-01",
                "claim_type": "手術醫療",
                "application_date": "2026-04-14",
                "claimed_amount": "2500"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "2500",
            "derivation": "施術場所為領有開業執照之診所(無病房):POL-B 第2條第二款承認「診所」、第五款門診手術得於醫院或診所施行;脂肪瘤切除術為附錄一第一級 5,000,依第8條門診手術按 50% 給付 = 2,500;病灶增大伴疼痛,屬治療疾病所必要,第15條不觸發;等待期已滿、非既往症。POL-B 為三張中唯一承認診所之保單(POL-A/C 醫院定義要求設有病房;惟 A/C 本無門診給付,此對照僅在 B 有意義)",
            "clause_basis": [
                "POL-B 第2條",
                "POL-B 第8條",
                "POL-B 附錄一"
            ]
        }
    },

    # --- 第六批(2026-09-08,人工檢核 4 v)+ 缺口 3 驗證案(2 v):第 14 日邊界 / 申請時效 / 剖腹產 / 非特約醫院 / 他保扣除 ×2 ---
    {
        "claim_id": "C066",
        "scenario": "邊界件:出院後恰第 14 日再住院視為同一次(POL-C 第13條;C055/C059 之間)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "莊雅芳",
                "diagnosis": "急性膽管炎(復發)",
                "icd_code": "K83.0",
                "admissions": [
                    {
                        "admission_date": "2026-08-01",
                        "discharge_date": "2026-08-05",
                        "icu_days": "0"
                    },
                    {
                        "admission_date": "2026-08-19",
                        "discharge_date": "2026-08-21",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "第 1 次住院靜脈抗生素(含自費抗生素)治療後出院;出院後同疾病復發再入院續以抗生素治療;以全民健康保險身分住院",
                "issue_date": "2026-08-21",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "莊雅芳",
                "receipt_date": "2026-08-21",
                "items": [
                    {
                        "item_name": "病房費(第1次)",
                        "amount": "12000"
                    },
                    {
                        "item_name": "藥費(第1次)",
                        "amount": "28000"
                    },
                    {
                        "item_name": "檢驗費(第1次)",
                        "amount": "12000"
                    },
                    {
                        "item_name": "病房費(第2次)",
                        "amount": "7500"
                    },
                    {
                        "item_name": "藥費(第2次)",
                        "amount": "20000"
                    },
                    {
                        "item_name": "檢驗費(第2次)",
                        "amount": "10000"
                    }
                ],
                "total_amount": "89500"
            },
            "claim_application": {
                "applicant_name": "莊雅芳",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-01-10",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-08-27",
                "claimed_amount": "89500"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "79500",
            "derivation": "起算約定:出院日不計,翌日起算第 1 日(SPEC §14)→ 08-05 出院、08-19 再住院為第 14 日,「十四日內」成立,依第13條視為同一次住院,第10條雜費限額合併:40,000 + 30,000 = 70,000 → min(70,000, 60,000)= 60,000;病房費逐日限額各段獨立:min(12,000, 3,000×5)= 12,000、min(7,500, 3,000×3)= 7,500(第8條);合計 79,500。若誤為第 15 日則各套 60,000 → 89,500,多給 10,000。第 10 日(C055)合併、第 14 日(本案)合併、第 15 日(C059)不合併,三點鎖死邊界",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第13條"
            ]
        }
    },
    {
        "claim_id": "C067",
        "scenario": "申請時效消滅件(POL-A 第14條;程序性條文)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "賴志誠",
                "diagnosis": "社區性肺炎",
                "icd_code": "J18.9",
                "admissions": [
                    {
                        "admission_date": "2024-04-06",
                        "discharge_date": "2024-04-10",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "抗生素治療後出院",
                "issue_date": "2024-04-10",
                "hospital_name": "平安紀念醫院"
            },
            "medical_receipt": {
                "hospital_name": "平安紀念醫院",
                "patient_name": "賴志誠",
                "receipt_date": "2024-04-10",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "7500"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "3200"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "2600"
                    }
                ],
                "total_amount": "13300"
            },
            "claim_application": {
                "applicant_name": "賴志誠",
                "policy_number": "POL-A-2026-01",
                "policy_effective_date": "2023-01-15",
                "claim_type": "住院醫療",
                "application_date": "2026-05-02",
                "claimed_amount": "15000"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "醫療內容無任何爭議:等待期已滿、非既往症,日額 2000×5 + 輔助 1000×5 = 15,000 本可核付;惟得為申請之日為 2024-04-10(出院、診斷書開立、收據開立同日,無起算歧義;即使自入院日 2024-04-06 起算亦已逾),至申請日 2026-05-02 已逾二年,依第14條保險金申請權因二年不行使而消滅 → 拒付;第34條為同旨之一般時效規定。唯一與醫療內容無關之拒付事由",
            "clause_basis": [
                "POL-A 第14條",
                "POL-A 第34條"
            ]
        }
    },
    {
        "claim_id": "C068",
        "scenario": "除外責任件:分娩手術含剖腹產(POL-B 第17條;陷阱)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "游淑娟",
                "diagnosis": "足月妊娠臀位",
                "icd_code": "O32.1",
                "admissions": [
                    {
                        "admission_date": "2026-06-14",
                        "discharge_date": "2026-06-18",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "胎位不正(臀位),行剖腹產,母嬰均安,無妊娠併發症",
                "issue_date": "2026-06-18",
                "hospital_name": "康寧聯合醫院"
            },
            "medical_receipt": {
                "hospital_name": "康寧聯合醫院",
                "patient_name": "游淑娟",
                "receipt_date": "2026-06-18",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "10000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "35000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "8000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "3200"
                    }
                ],
                "total_amount": "56200"
            },
            "claim_application": {
                "applicant_name": "游淑娟",
                "policy_number": "POL-B-2026-01",
                "policy_effective_date": "2024-10-01",
                "claim_type": "手術醫療",
                "application_date": "2026-06-24",
                "claimed_amount": "35000"
            }
        },
        "truth": {
            "decision": "deny",
            "approved_amount": "0",
            "derivation": "剖腹產為因分娩所接受之手術,第17條明文「含剖腹產」除外。但書四款(子宮外孕、葡萄胎、前置胎盤、子癲前症及子癇症)為**列舉而非例示**,臀位雖在醫學上可稱妊娠相關狀況,不在列舉之內,不得類推適用;第30條疑義有利被保險人之解釋原則以條文有疑義為前提,列舉明確即無疑義,不得據以撐開但書 → 拒付。剖腹產雖為手術且未列附錄一,除外責任優先,無須進入第11條未列表核定",
            "clause_basis": [
                "POL-B 第17條"
            ]
        }
    },
    {
        "claim_id": "C069",
        "scenario": "非特約醫院之 65% 給付件(POL-C 第12條後款,approve)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "吳佩蓉",
                "diagnosis": "急性闌尾炎",
                "icd_code": "K35.8",
                "admissions": [
                    {
                        "admission_date": "2026-05-06",
                        "discharge_date": "2026-05-08",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "行闌尾切除術;病患具全民健康保險身分,惟本院非全民健康保險特約醫院,全額自費",
                "issue_date": "2026-05-08",
                "hospital_name": "康泰醫院(非健保特約)"
            },
            "medical_receipt": {
                "hospital_name": "康泰醫院(非健保特約)",
                "patient_name": "吳佩蓉",
                "receipt_date": "2026-05-08",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "9000"
                    },
                    {
                        "item_name": "手術費",
                        "amount": "18000"
                    },
                    {
                        "item_name": "麻醉費",
                        "amount": "4000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "5000"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "3000"
                    }
                ],
                "total_amount": "39000"
            },
            "claim_application": {
                "applicant_name": "吳佩蓉",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-04-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-05-14",
                "claimed_amount": "39000"
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "24050",
            "derivation": "病患有健保身分,但就診醫院非全民健康保險特約醫院,依第2條第三款不構成「全民健康保險身分就診」,第12條後款「就診醫院非全民健康保險特約醫院者,按百分之六十五給付」觸發;先逐項套限額:病房 min(9,000, 3,000×3)= 9,000(恰在限額,全額給,第8條);手術費+麻醉費 22,000 vs 闌尾切除術附錄一第一級 80,000×25% = 20,000 → 20,000(第9條,麻醉費一併受限);雜費 min(8,000, 60,000)= 8,000(第10條);小計 37,000 × 65% = 24,050。C010/C035 為「被保險人非健保身分」,本案為同條另一分支",
            "clause_basis": [
                "POL-C 第2條",
                "POL-C 第8條",
                "POL-C 第9條",
                "POL-C 第10條",
                "POL-C 第12條",
                "POL-C 附錄一"
            ]
        }
    },
    {
        "claim_id": "C070",
        "scenario": "他保扣除件:餘額未逾限額(POL-C 第14條,approve)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "蕭國榮",
                "diagnosis": "急性胰臟炎",
                "icd_code": "K85.90",
                "admissions": [
                    {
                        "admission_date": "2026-07-10",
                        "discharge_date": "2026-07-16",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "禁食、靜脈輸液與止痛治療,以全民健康保險身分於特約醫院住院",
                "issue_date": "2026-07-16",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "蕭國榮",
                "receipt_date": "2026-07-16",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "18000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "42000"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "28000"
                    }
                ],
                "total_amount": "88000",
                "is_copy": True
            },
            "claim_application": {
                "applicant_name": "蕭國榮",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-02-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-07-22",
                "claimed_amount": "88000",
                "other_insurer_paid": {
                    "room_fee": "0",
                    "surgery_fee": "0",
                    "misc_fee": "30000",
                    "ambulance_fee": "0",
                    "unallocated": "0"
                }
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "58000",
            "derivation": "收據為影本並蓋「與正本相符」戳記(正本已用於申領其他保險),依第14條僅就正本所載金額扣除其他保險已給付部分後之餘額,於各項限額內給付;申請書載已獲他保理賠住院雜費 30,000;先逐桶扣、再套限額:雜費(藥費+檢驗費)70,000 − 30,000 = 40,000 → min(40,000, 60,000)= 40,000(第10條);病房費 18,000 − 0 → min(18,000, 3,000×7)= 18,000(第8條);合計 58,000;申請 88,000(毛額)超過部分不予給付。若先截再扣(60,000 − 30,000 = 30,000 + 18,000 = 48,000)會少給 10,000",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第14條"
            ]
        }
    },
    {
        "claim_id": "C071",
        "scenario": "他保扣除件:餘額仍逾限額(POL-C 第14條,approve;與 C070 對照)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "蕭國榮",
                "diagnosis": "急性胰臟炎",
                "icd_code": "K85.90",
                "admissions": [
                    {
                        "admission_date": "2026-07-10",
                        "discharge_date": "2026-07-16",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "禁食、靜脈輸液與止痛治療,以全民健康保險身分於特約醫院住院",
                "issue_date": "2026-07-16",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "蕭國榮",
                "receipt_date": "2026-07-16",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "18000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "42000"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "28000"
                    }
                ],
                "total_amount": "88000",
                "is_copy": True
            },
            "claim_application": {
                "applicant_name": "蕭國榮",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-02-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-07-22",
                "claimed_amount": "88000",
                "other_insurer_paid": {
                    "room_fee": "0",
                    "surgery_fee": "0",
                    "misc_fee": "5000",
                    "ambulance_fee": "0",
                    "unallocated": "0"
                }
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "78000",
            "derivation": "收據為影本並蓋「與正本相符」戳記,申請書載已獲他保理賠住院雜費 5,000;同 C070 之順序:雜費 70,000 − 5,000 = 65,000 → min(65,000, 60,000)= 60,000(第10條);病房費 min(18,000, 21,000)= 18,000(第8條);合計 78,000;申請 88,000 超過部分不予給付。先截再扣(60,000 − 5,000 = 55,000 + 18,000 = 73,000)會少給 5,000。與 C070 同一組文件,唯一變數是他保金額(30,000 vs 5,000):一案扣後未逾限額、一案扣後仍逾,兩案合起來鎖死「先扣再截」",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第14條"
            ]
        }
    },

    # --- 第七批(2026-09-08,人工檢核:C072 必要 v;C073/C074 非必要不納入)---
    {
        "claim_id": "C072",
        "scenario": "他保扣除件:病房桶被扣、雜費桶未扣(POL-C 第14條,approve;測「逐桶」)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "蕭國榮",
                "diagnosis": "急性胰臟炎",
                "icd_code": "K85.90",
                "admissions": [
                    {
                        "admission_date": "2026-07-10",
                        "discharge_date": "2026-07-16",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "禁食、靜脈輸液與止痛治療,以全民健康保險身分於特約醫院住院",
                "issue_date": "2026-07-16",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "蕭國榮",
                "receipt_date": "2026-07-16",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "18000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "42000"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "28000"
                    }
                ],
                "total_amount": "88000",
                "is_copy": True
            },
            "claim_application": {
                "applicant_name": "蕭國榮",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-02-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-07-22",
                "claimed_amount": "88000",
                "other_insurer_paid": {
                    "room_fee": "12000",
                    "surgery_fee": "0",
                    "misc_fee": "0",
                    "ambulance_fee": "0",
                    "unallocated": "0"
                }
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "66000",
            "derivation": "收據為影本並蓋「與正本相符」戳記,申請書載已獲他保理賠病房費用 12,000;第14條之扣除須逐桶(第8~11條各項):病房費 18,000 − 12,000 = 6,000 → min(6,000, 3,000×7)= 6,000(第8條);雜費(藥費+檢驗費)70,000 − 0 → min(70,000, 60,000)= 60,000(第10條);合計 66,000;申請 88,000 超過部分不予給付。若把他保 12,000 誤扣在雜費桶(70,000 − 12,000 = 58,000;58,000 + 18,000 = 76,000)或整包合計扣(88,000 − 12,000 再逐項截)皆錯。與 C070/C071 同一組文件,只換他保落桶;順序考點由 C070/C071 承擔(本案先截再扣亦得 66,000)",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第14條"
            ]
        }
    },
    # --- SPEC-y(2026-10-06,使用者核准:C073「不扣」;與 C072 同一組文件,只改收據正本 + 他保型態)---
    {
        "claim_id": "C073",
        "scenario": "他保為日額型:收據正本未用於他保,不扣除(POL-C 第14條,approve;測「觸發要件」)",
        "documents": {
            "diagnosis_certificate": {
                "patient_name": "蕭國榮",
                "diagnosis": "急性胰臟炎",
                "icd_code": "K85.90",
                "admissions": [
                    {
                        "admission_date": "2026-07-10",
                        "discharge_date": "2026-07-16",
                        "icu_days": "0"
                    }
                ],
                "treatment_summary": "禁食、靜脈輸液與止痛治療,以全民健康保險身分於特約醫院住院",
                "issue_date": "2026-07-16",
                "hospital_name": "安心綜合醫院"
            },
            "medical_receipt": {
                "hospital_name": "安心綜合醫院",
                "patient_name": "蕭國榮",
                "receipt_date": "2026-07-16",
                "items": [
                    {
                        "item_name": "病房費",
                        "amount": "18000"
                    },
                    {
                        "item_name": "藥費",
                        "amount": "42000"
                    },
                    {
                        "item_name": "檢驗費",
                        "amount": "28000"
                    }
                ],
                "total_amount": "88000",
                "is_copy": False
            },
            "claim_application": {
                "applicant_name": "蕭國榮",
                "policy_number": "POL-C-2026-01",
                "policy_effective_date": "2025-02-01",
                "claim_type": "實支實付住院醫療",
                "application_date": "2026-07-22",
                "claimed_amount": "88000",
                "other_insurer_paid": {
                    "room_fee": "0",
                    "surgery_fee": "0",
                    "misc_fee": "0",
                    "ambulance_fee": "0",
                    "unallocated": "14000"
                }
            }
        },
        "truth": {
            "decision": "approve",
            "approved_amount": "78000",
            "derivation": "收據為正本,申請書載他公司日額型保險已給付 14,000(2,000×7 日,無法歸入任何收據項目);第14條之扣除以「收據正本已用於申領其他保險給付」為觸發要件,日額型給付不以收據正本為要件、本件正本亦未用於他保,要件不成立 → 不扣除(住院醫療費用保險單示範條款之扣除對象亦限實支實付型他保)。病房費 min(18,000, 3,000×7)= 18,000(第8條);雜費(藥費+檢驗費)min(70,000, 60,000)= 60,000(第10條);合計 78,000;申請 88,000 超過部分不予給付。若誤扣 14,000 得 64,000、因他保欄位誤轉人工則判定錯。與 C072 同一組文件,只換收據正本與他保型態(2026-10-06 依使用者核准新增,SPEC-y)",
            "clause_basis": [
                "POL-C 第8條",
                "POL-C 第10條",
                "POL-C 第14條"
            ]
        }
    },
]


def all_cases() -> list[dict[str, Any]]:
    """全部案件:手寫 43 案(C001–C010、C041–C073)+ 程式化擴量 30 案(C011–C040),依編號排序。"""
    from app.datagen.variants import build_variants

    cases = sorted(CASES + build_variants(), key=lambda c: c["claim_id"])
    for c in cases:
        _apply_document_defaults(c["documents"])
    return cases


ZERO_OTHER_INSURER_PAID = {"room_fee": "0", "surgery_fee": "0", "misc_fee": "0", "ambulance_fee": "0", "unallocated": "0"}


def _apply_document_defaults(docs: dict[str, Any]) -> None:
    """未明寫之欄位補預設值(單一定義,固定解法 4):收據正本、未向他保申請。"""
    if docs.get("medical_receipt") is not None:
        docs["medical_receipt"].setdefault("is_copy", False)
    if docs.get("claim_application") is not None:
        docs["claim_application"].setdefault("other_insurer_paid", dict(ZERO_OTHER_INSURER_PAID))
    docs.setdefault("incident_statement", None)  # 第四類文件屬選擇性,預設缺席


def generate(out_dir: Path = CLAIMS_DIR, force: bool = False) -> list[Path]:
    """將案件規格寫入 data/claims/C###/{claim,truth}.json,回傳寫出的路徑。

    預設不覆蓋既有案件目錄(批次防護:CLAUDE.md 固定解法 11);
    force=True 才會刪除並重建——僅刪規格中存在的 C### 目錄,不碰其他東西。
    """
    from app.datagen.bundle import bundle_from_documents

    written: list[Path] = []
    for case in all_cases():
        docs = case["documents"]
        if all(docs[k] is not None for k in ("diagnosis_certificate", "medical_receipt", "claim_application")):
            bundle_from_documents(case["claim_id"], docs)  # 落盤前契約驗證:資料與 schema 不得漂移(A17)
        case_dir = out_dir / case["claim_id"]
        if case_dir.exists():
            if not force:
                continue
            shutil.rmtree(case_dir)
        case_dir.mkdir(parents=True)
        meta = {"claim_id": case["claim_id"], "scenario": case["scenario"], "synthetic": True}
        claim_path = case_dir / "claim.json"
        truth_path = case_dir / "truth.json"
        claim_path.write_text(
            json.dumps({**meta, "documents": case["documents"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        truth_path.write_text(
            json.dumps({**meta, **case["truth"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.extend([claim_path, truth_path])
    return written


if __name__ == "__main__":
    force = "--force" in sys.argv[1:]
    paths = generate(force=force)
    print(f"寫出 {len(paths)} 個檔案" + ("(--force 重建)" if force else "(既有目錄已跳過;重建請加 --force)"))
