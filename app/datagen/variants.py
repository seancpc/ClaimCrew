"""擴量案件 C011–C040:模板×參數的程式化生成(全合成資料,零 PHI)。

與 cases.py 的分工:首批 10 案為手寫凍結;本檔以確定性計算器生成 30 案,
真值金額與條款參數同源(POLICY_PARAMS),日數慣例「含入出院日」寫死於
_days();推導式自動組出。真值仍屬人類保留區:提議值,經使用者抽驗後凍結。

執行:python3 -m app.datagen.variants(只列摘要表)
實際落盤由 cases.generate 統一進行(python3 -m app.datagen.cases)。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

# --- 條款參數(單一定義;與 data/policies/POL-*.md 條文一致)---
POLICY_PARAMS: dict[str, dict[str, Any]] = {
    "POL-A": {
        "daily": 2000, "icu_daily": 4000, "aux_daily": 1000,
        "surgery": {1: 10000, 2: 25000, 3: 50000},
        "outpatient_visit": 500, "outpatient_visits_cap": 6,
        "per_stay_cap_days": 90, "annual_cap_days": 180, "wait_days": 30, "special_wait_days": 90,
    },
    "POL-B": {
        "surgery": {1: 5000, 2: 15000, 3: 30000, 4: 60000, 5: 100000},
        "outpatient_pct": Decimal("0.5"), "multi_surgery_second_pct": Decimal("0.5"),
        "solace_daily": 1000, "solace_cap_days": 30,
        "wait_days": 30,
    },
    "POL-C": {
        "room_daily_cap": 3000, "surgery_base": 80000,
        "surgery_pct": {1: Decimal("0.25"), 2: Decimal("0.5"), 3: Decimal("1.0")},
        "misc_cap": 60000, "ambulance_cap": 2000, "non_nhi_factor": Decimal("0.65"),
        "wait_days": 30,
    },
}

NAMES = ["周家豪", "鄭雅文", "何俊傑", "謝佩珊", "郭宗翰", "曾麗娟", "賴冠宇", "簡淑惠",
         "潘志偉", "游雅萍", "洪國強", "葉怡君", "徐建成", "邱美玲", "蘇明輝", "盧靜宜",
         "江柏翰", "呂秀蘭", "馬俊宏", "施雅惠", "宋志明", "唐佳蓉", "馮偉倫", "白曉萍",
         "康承翰", "程冠廷", "袁淑貞", "夏志豪", "溫雅築", "藍振宇"]
HOSPITALS = ["安心綜合醫院", "平安紀念醫院", "康寧聯合醫院"]


def _days(admission: str, discharge: str) -> int:
    """住院日數:含入出院日(2026-08-30 使用者定案)。"""
    a, d = date.fromisoformat(admission), date.fromisoformat(discharge)
    return (d - a).days + 1


def _fmt(n: int | Decimal) -> str:
    return str(int(n))


def _base_docs(i: int, policy: str, diagnosis: str, icd: str, adm: str, dis: str,
               summary: str, items: list[tuple[str, int]], effective: str,
               claim_type: str, claimed: int, hospital: str | None = None,
               icu_days: int = 0) -> dict[str, Any]:
    h = hospital or HOSPITALS[i % len(HOSPITALS)]
    total = sum(a for _, a in items)
    dis_d = date.fromisoformat(dis)
    return {
        "diagnosis_certificate": {
            "patient_name": NAMES[i], "diagnosis": diagnosis, "icd_code": icd,
            "admissions": [{"admission_date": adm, "discharge_date": dis, "icu_days": str(icu_days)}],
            "treatment_summary": summary,
            "issue_date": dis, "hospital_name": h,
        },
        "medical_receipt": {
            "hospital_name": h, "patient_name": NAMES[i], "receipt_date": dis,
            "items": [{"item_name": n, "amount": str(a)} for n, a in items],
            "total_amount": str(total),
        },
        "claim_application": {
            "applicant_name": NAMES[i], "policy_number": f"{policy}-2026-01",
            "policy_effective_date": effective, "claim_type": claim_type,
            "application_date": (dis_d + timedelta(days=6)).isoformat(),
            "claimed_amount": str(claimed),
        },
    }


def _pol_a_amount(days: int, icu_days: int = 0, surgery_level: int | None = None,
                  surgery_name: str = "") -> tuple[int, str]:
    p = POLICY_PARAMS["POL-A"]
    counted = min(days, p["per_stay_cap_days"])
    ward = p["daily"] * (counted - icu_days) + p["icu_daily"] * icu_days
    aux = p["aux_daily"] * counted
    surg = p["surgery"][surgery_level] if surgery_level else 0
    parts = [f"日額 2000×{counted - icu_days}"]
    if icu_days:
        parts.append(f"加護 4000×{icu_days}(第8條,不重複計日額)")
    parts.append(f"輔助 1000×{counted}(第11條)")
    if surgery_level:
        parts.append(f"手術「{surgery_name}」屬附錄一第{surgery_level}級 {_fmt(surg)}(第10條)")
    cap_note = f";逾第13條上限認列 {counted} 日" if days > counted else ""
    deriv = f"含入出院日 {days} 日{cap_note};" + " + ".join(parts) + f" = {_fmt(ward + aux + surg)}"
    return ward + aux + surg, deriv


def _pol_b_amount(level: int, outpatient: bool, days: int = 0, surgery_name: str = "") -> tuple[int, str]:
    p = POLICY_PARAMS["POL-B"]
    base = p["surgery"][level]
    surg = int(Decimal(base) * p["outpatient_pct"]) if outpatient else base
    solace = 0 if outpatient else p["solace_daily"] * min(days, p["solace_cap_days"])
    kind = (f"門診手術「{surgery_name}」(附錄一第{level}級){_fmt(base)}×50%(第8條)" if outpatient
            else f"住院手術「{surgery_name}」(附錄一第{level}級){_fmt(base)}(第7條)")
    deriv = kind + (f" + 慰問 1000×{min(days, 30)}(第12條)" if solace else "") + f" = {_fmt(surg + solace)}"
    return surg + solace, deriv


def _pol_c_amount(days: int, room: int, surgery_fee: int, surgery_level: int | None,
                  misc: int, ambulance: int = 0, nhi: bool = True,
                  surgery_name: str = "") -> tuple[int, str]:
    p = POLICY_PARAMS["POL-C"]
    room_paid = min(room, p["room_daily_cap"] * days)
    parts = [f"病房費 min({_fmt(room)}, 3000×{days})={_fmt(room_paid)}(第8條)"]
    surg_paid = 0
    if surgery_level:
        cap = int(Decimal(p["surgery_base"]) * p["surgery_pct"][surgery_level])
        surg_paid = min(surgery_fee, cap)
        parts.append(f"手術費「{surgery_name}」(附錄一第{surgery_level}級)min({_fmt(surgery_fee)}, 80000×{p['surgery_pct'][surgery_level]})={_fmt(surg_paid)}(第9條)")
    misc_paid = min(misc, p["misc_cap"])
    parts.append(f"雜費 min({_fmt(misc)}, 60000)={_fmt(misc_paid)}(第10條)")
    amb_paid = min(ambulance, p["ambulance_cap"])
    if ambulance:
        parts.append(f"救護車 min({_fmt(ambulance)}, 2000)={_fmt(amb_paid)}(第11條)")
    subtotal = room_paid + surg_paid + misc_paid + amb_paid
    if nhi:
        return subtotal, ";".join(parts) + f";合計 {_fmt(subtotal)}"
    total = int(Decimal(subtotal) * p["non_nhi_factor"])
    return total, ";".join(parts) + f";小計 {_fmt(subtotal)} ×65%(第12條非健保身分)= {_fmt(total)}"


def build_variants() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    i = 0

    def add(scenario: str, docs: dict[str, Any], decision: str, amount: int, deriv: str,
            basis: list[str]) -> None:
        nonlocal i
        cases.append({
            "claim_id": f"C{11 + i:03d}", "scenario": scenario, "documents": docs,
            "truth": {"decision": decision, "approved_amount": str(amount),
                      "derivation": deriv, "clause_basis": basis},
        })
        i += 1

    # --- T1:POL-A 單純住院 approve ×4(日數變化)---
    t1 = [("支氣管肺炎", "J18.0", "2026-03-15", "2026-03-19", "抗生素治療後穩定出院"),
          ("泌尿道感染", "N39.0", "2026-04-02", "2026-04-08", "靜脈抗生素治療"),
          ("急性膽管炎", "K83.0", "2026-05-11", "2026-05-22", "抗生素與膽道引流治療"),
          ("蜂窩性組織炎", "L03.116", "2026-06-01", "2026-06-20", "清創換藥與抗生素治療")]
    for dx, icd, adm, dis, s in t1:
        d = _days(adm, dis)
        amt, deriv = _pol_a_amount(d)
        docs = _base_docs(i, "POL-A", dx, icd, adm, dis, s,
                          [("病房費", 1500 * d), ("藥費", 900 * d)], "2024-10-01", "住院醫療", amt)
        add(f"順利件:POL-A 住院 {d} 日", docs, "approve", amt, deriv,
            ["POL-A 第7條", "POL-A 第11條"])

    # --- T2:POL-A 住院+手術 approve ×3(等級 1/2/3)---
    t2 = [(1, "急性闌尾炎", "K35.8", "2026-04-05", "2026-04-08", "行闌尾切除術"),
          (2, "腰椎椎間盤突出", "M51.26", "2026-05-02", "2026-05-08", "行椎間盤切除術"),
          (3, "退化性髖關節炎", "M16.1", "2026-06-10", "2026-06-21", "行全髖關節置換術")]
    for lv, dx, icd, adm, dis, s in t2:
        d = _days(adm, dis)
        surgery_name = s.replace("行", "").rstrip("。")
        amt, deriv = _pol_a_amount(d, surgery_level=lv, surgery_name=surgery_name)
        docs = _base_docs(i, "POL-A", dx, icd, adm, dis, s,
                          [("病房費", 1500 * d), ("手術費", 8000 * lv * 2), ("藥費", 800 * d)],
                          "2024-06-15", "住院醫療", amt)
        add(f"順利件:POL-A 住院+第{lv}級手術", docs, "approve", amt, deriv,
            ["POL-A 第7條", "POL-A 第10條", "POL-A 第11條", "POL-A 附錄一"])

    # --- T3:POL-A 含加護病房 approve ×2 ---
    t3 = [("敗血症", "A41.9", "2026-03-20", "2026-04-02", 4, "加護病房治療四日後轉普通病房"),
          ("急性呼吸衰竭", "J96.00", "2026-05-15", "2026-06-03", 7, "插管治療,加護病房七日")]
    for dx, icd, adm, dis, icu, s in t3:
        d = _days(adm, dis)
        amt, deriv = _pol_a_amount(d, icu_days=icu)
        docs = _base_docs(i, "POL-A", dx, icd, adm, dis, f"{s},共住院{d}日",
                          [("病房費", 2000 * d), ("加護病房費", 8000 * icu), ("藥費", 1200 * d)],
                          "2024-09-01", "住院醫療", amt, icu_days=icu)
        add(f"金額計算件:POL-A 含 ICU {icu} 日", docs, "approve", amt, deriv,
            ["POL-A 第7條", "POL-A 第8條", "POL-A 第11條"])

    # --- T4:等待期未滿 deny ×2 ---
    t4 = [("急性咽喉炎併扁桃腺膿瘍", "J36", "2026-04-10", "2026-04-13", "2026-03-25"),
          ("急性膽囊炎", "K81.0", "2026-07-05", "2026-07-09", "2026-06-20")]
    for dx, icd, adm, dis, eff in t4:
        gap = (date.fromisoformat(adm) - date.fromisoformat(eff)).days
        docs = _base_docs(i, "POL-A", dx, icd, adm, dis, "住院治療後出院",
                          [("病房費", 6000), ("藥費", 2500)], eff, "住院醫療", 3000 * _days(adm, dis))
        add("等待期未滿件:POL-A", docs, "deny", 0,
            f"生效 {eff} 至住院 {adm} 僅 {gap} 日,未滿 30 日疾病等待期(第22條)",
            ["POL-A 第22條"])

    # --- T5:特定疾病延長等待期未滿 deny ×1 ---
    docs = _base_docs(i, "POL-A", "急性心肌梗塞", "I21.9", "2026-05-20", "2026-05-28",
                      "心導管治療後出院", [("病房費", 18000), ("手術費", 45000)],
                      "2026-03-10", "住院醫療", 77000)
    add("特定疾病等待期未滿件:POL-A", docs, "deny", 0,
        "缺血性心臟病屬附錄二特定疾病;生效 2026-03-10 至住院 2026-05-20 僅 71 日,未滿 90 日延長等待期(第23條)",
        ["POL-A 第23條", "POL-A 附錄二"])

    # --- T6:既往症 deny ×2 ---
    t6 = [("慢性阻塞性肺病急性惡化", "J44.1", "2026-04-18", "2026-04-25",
           "病患 COPD 病史三年,長期胸腔內科門診追蹤,本次急性惡化住院",
           "2025-08-01", [("病房費", 12000), ("藥費", 6800)], 24000),
          ("肝硬化併腹水", "K74.60", "2026-06-08", "2026-06-16",
           "病患肝硬化確診於四年前,定期本院肝膽腸胃科追蹤,本次因腹水住院",
           "2024-11-15", [("病房費", 15000), ("藥費", 9400), ("檢驗費", 3200)], 27000)]
    for dx, icd, adm, dis, s, eff, items, claimed in t6:
        docs = _base_docs(i, "POL-A", dx, icd, adm, dis, s, items, eff, "住院醫療", claimed)
        add("既往症拒付件:POL-A", docs, "deny", 0,
            f"診斷書載明「{dx}」於投保({eff})前已確診並長期門診追蹤,"
            "屬投保前二年內已就診之既往症及其併發症(第2條、第18條)",
            ["POL-A 第2條", "POL-A 第18條"])

    # --- T7:文件缺漏 manual ×2(缺診斷書/缺申請書)---
    for missing, keep_note in [("diagnosis_certificate", "缺診斷證明書"), ("claim_application", "缺理賠申請書")]:
        docs = _base_docs(i, "POL-A", "腹股溝疝氣", "K40.90", "2026-05-06", "2026-05-08",
                          "行疝氣修補術", [("病房費", 4500), ("手術費", 15000)],
                          "2024-12-01", "住院醫療", 24000)
        docs[missing] = None
        if missing == "claim_application":
            add(f"文件缺漏件:{keep_note}", docs, "manual_review", 0,
                "缺理賠申請書,無保單號碼可資歸戶,適用保單待確認(三張保單之應備文件/補正條號各異);"
                "以三張保單共通之應備四項文件通知補件;Intake 中止不進入判定",
                [])
        else:
            add(f"文件缺漏件:{keep_note}", docs, "manual_review", 0,
                f"應備文件{keep_note}(第25條),依第26條通知補正;Intake 中止不進入判定",
                ["POL-A 第25條", "POL-A 第26條"])

    # --- T8:POL-B 門診手術 approve ×2 ---
    t8 = [(2, "白內障", "H25.9", "2026-04-22", "行白內障晶體置換術(門診)"),
          (1, "體表脂肪瘤", "D17.9", "2026-06-03", "行脂肪瘤切除術(門診)")]
    for lv, dx, icd, day, s in t8:
        surgery_name = s.replace("行", "").replace("(門診)", "").rstrip("。")
        amt, deriv = _pol_b_amount(lv, outpatient=True, surgery_name=surgery_name)
        docs = _base_docs(i, "POL-B", dx, icd, day, day, s,
                          [("手術費", 20000 * lv), ("麻醉費", 2000)], "2025-01-15", "手術醫療", amt)
        add(f"順利件:POL-B 門診手術第{lv}級", docs, "approve", amt, deriv,
            ["POL-B 第8條", "POL-B 附錄一"])

    # --- T9:POL-B 住院手術+慰問金 approve ×2 ---
    t9 = [(3, "膽結石併急性膽囊炎", "K80.00", "2026-03-08", "2026-03-12", "行膽囊切除術"),
          (5, "冠狀動脈三條血管疾病", "I25.10", "2026-05-25", "2026-06-30", "行冠狀動脈繞道手術,術後復健")]
    for lv, dx, icd, adm, dis, s in t9:
        d = _days(adm, dis)
        surgery_name = s.split(",")[0].replace("行", "").rstrip("。")
        amt, deriv = _pol_b_amount(lv, outpatient=False, days=d, surgery_name=surgery_name)
        docs = _base_docs(i, "POL-B", dx, icd, adm, dis, f"{s},住院{d}日",
                          [("病房費", 2000 * d), ("手術費", 30000 * lv), ("麻醉費", 8000)],
                          "2024-11-10", "手術醫療", amt)
        add(f"順利件:POL-B 住院手術第{lv}級(住院{d}日,慰問上限30日)", docs, "approve", amt, deriv,
            ["POL-B 第7條", "POL-B 第12條", "POL-B 附錄一"])

    # --- T10:POL-B 僅診斷性處置 deny ×1 ---
    docs = _base_docs(i, "POL-B", "大腸瘜肉", "K63.5", "2026-04-30", "2026-04-30",
                      "行大腸鏡檢查,發現瘜肉並於檢查中切除送驗",
                      [("檢查費", 9000), ("病理費", 1500)], "2025-02-01", "手術醫療", 15000)
    add("診斷性處置件:POL-B 大腸鏡瘜肉切除", docs, "manual_review", 0,
        "第2條第三款正面表列「內視鏡輔助切除」為手術,排除者為「單純之診斷性處置」——瘜肉一經切除即非單純檢查,"
        "併第30條疑義有利被保險人,宜認定已構成手術;惟該術式未列於附錄一(第四級「大腸部分切除術」非同一術式),"
        "應依第11條由保險公司比照核定等級 → 轉人工",
        ["POL-B 第2條", "POL-B 第11條", "POL-B 附錄一"])

    # --- T11:POL-C 實支實付 approve ×4(限額組合)---
    t11: list[tuple[Any, ...]] = [
        ("急性胰臟炎", "K85.90", "2026-03-12", "2026-03-19", 2800, 0, None, "", 31000, 0, True),
        ("股骨幹骨折", "S72.30", "2026-04-15", "2026-04-24", 3500, 62000, 2, "骨折開放復位內固定術", 41500, 1800, True),
        ("急性心肌炎", "I40.9", "2026-05-28", "2026-06-06", 2500, 0, None, "", 71200, 2600, True),
        ("退化性髖關節炎", "M16.1", "2026-06-15", "2026-06-25", 4200, 95000, 3, "全髖關節置換術", 52000, 0, False)]
    for dx, icd, adm, dis, room_d, surg, lv, sname, misc, amb, nhi in t11:
        d = _days(adm, dis)
        room = room_d * d
        amt, deriv = _pol_c_amount(d, room, surg, lv, misc, amb, nhi, surgery_name=sname)
        items = [("病房費", room)]
        if surg:
            items.append(("手術費(含麻醉)", surg))
        items += [("醫療雜費", misc)]
        if amb:
            items.append(("救護車費", amb))
        s = "以全民健康保險身分住院診療" if nhi else "自費身分入住非健保特約醫院"
        if sname:
            s = f"行{sname},{s}"
        claimed = room + surg + misc + amb  # 申請金額=收據總額(2026-09-01 檢核修正)
        docs = _base_docs(i, "POL-C", dx, icd, adm, dis, s, items, "2024-07-20",
                          "實支實付住院醫療", claimed)
        tag = "非健保65%" if not nhi else ("超限截斷" if room_d > 3000 or misc > 60000 or (surg and lv) else "限額內")
        add(f"實支實付件:POL-C {tag}(住院{d}日)", docs, "approve", amt, deriv,
            ["POL-C 第8條"] + (["POL-C 第9條", "POL-C 附錄一"] if lv else []) + ["POL-C 第10條"]
            + (["POL-C 第11條"] if amb else []) + ([] if nhi else ["POL-C 第12條"]))

    # --- T12:POL-C 雜費含負面項目 approve ×1 ---
    d = _days("2026-07-02", "2026-07-06")
    room = 2200 * d
    misc_valid = 18700
    amt, deriv = _pol_c_amount(d, room, 0, None, misc_valid)
    docs = _base_docs(i, "POL-C", "急性腎盂腎炎", "N10", "2026-07-02", "2026-07-06",
                      "以全民健康保險身分住院,靜脈抗生素治療",
                      [("病房費", room), ("藥費", 12200), ("檢驗費", 6500),
                       ("診斷證明書費", 400), ("掛號費", 150), ("看護費", 8000)],
                      "2025-03-05", "實支實付住院醫療", amt + 8550)
    add("雜費負面項目件:POL-C(證明書費/掛號費/看護費不計)", docs, "approve", amt,
        deriv + ";診斷證明書費 400、掛號費 150、看護費 8000 屬第10條負面列舉不計入",
        ["POL-C 第8條", "POL-C 第10條"])

    # --- T13:除外責任 deny ×3(酒駕/美容/牙科)---
    t13 = [("POL-A", "頭部外傷併腦震盪", "S06.0X0", "2026-05-30", "2026-06-03",
            "騎乘機車自摔送醫,頭部外傷併腦震盪,住院觀察治療",
            "警方紀錄載明騎乘機車自摔時吐氣酒精濃度 0.42 mg/L,超過道路交通法令規定標準(0.15 mg/L),所受傷害屬第16條酒駕除外事由 → 拒付;"
            "診斷書僅載醫療事實、未提酒駕,與警方紀錄互補而非矛盾", ["POL-A 第16條"]),
           ("POL-B", "雙側眼瞼下垂(美容目的)", "H02.401", "2026-04-08", "2026-04-08",
            "行雙眼皮整形手術(非疾病治療必要)",
            "非治療必要之美容整形手術屬除外(第15條),無意外傷害例外情事", ["POL-B 第15條"]),
           ("POL-A", "慢性牙周病", "K05.30", "2026-06-22", "2026-06-23",
            "住院行牙周翻瓣手術",
            "牙科處置屬除外(第20條),非因意外傷害所致,不適用例外", ["POL-A 第20條"])]
    for pol, dx, icd, adm, dis, summary, deriv, basis in t13:
        docs = _base_docs(i, pol, dx, icd, adm, dis, summary,
                          [("病房費", 6000), ("手術費", 22000)], "2024-08-15",
                          "住院醫療" if pol == "POL-A" else "手術醫療", 28000)
        if "酒駕" in deriv:
            docs["incident_statement"] = {
                "source": "police_record", "incident_date": adm,
                "statement_text": f"{adm} 當事人騎乘普通重型機車行經市區道路自行摔倒,經警方到場實施吐氣酒精濃度測試為 0.42 mg/L,"
                                  "超過道路交通安全規則規定標準(0.15 mg/L),依法舉發;當事人送醫治療。",
            }
        add(f"除外責任件:{pol}", docs, "deny", 0, deriv, basis)

    # --- T14:未列表手術 manual ×1 ---
    docs = _base_docs(i, "POL-B", "肝腫瘤", "D13.4", "2026-06-28", "2026-06-30",
                      "行肝腫瘤射頻消融術(經皮微創,未列於手術等級表)",
                      [("病房費", 6000), ("手術費", 52000)], "2025-04-01", "手術醫療", 30000)
    add("未列表手術件:POL-B 射頻消融", docs, "manual_review", 0,
        "射頻消融是否落入第2條第三款手術定義本身即屬爭點;縱認構成手術,亦未列於附錄一,"
        "依第11條由保險公司比照核定等級,屬人工裁量 → 轉人工",
        ["POL-B 第2條", "POL-B 第11條", "POL-B 附錄一"])

    return cases


if __name__ == "__main__":
    for c in build_variants():
        t = c["truth"]
        print(f"{c['claim_id']}  {t['decision']:<14} {t['approved_amount']:>8}  {c['scenario']}")
    print(f"共 {len(build_variants())} 案")
