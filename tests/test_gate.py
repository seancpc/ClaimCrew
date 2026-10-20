"""Gate 檢核與條款索引的手算案例測試。"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.gate.checks import run_gate
from app.gate.policy_index import cn_to_int, load_policy_index, normalize_clause_no
from app.schemas.claim import (
    AdjudicationResult,
    Admission,
    CalcKind,
    CalcLine,
    ClaimApplication,
    ClaimBundle,
    ClaimDecision,
    DiagnosisCertificate,
    ExtractedField,
    MedicalReceipt,
    OtherInsurerPaid,
    PolicyCitation,
)


def _f(v: str = "x", c: float = 0.95) -> ExtractedField:
    return ExtractedField(value=v, confidence=c)


def _bundle(policy_number: str = "POL-A-2026-01", low_conf: list[str] | None = None) -> ClaimBundle:
    return ClaimBundle(
        claim_id="T1",
        diagnosis_certificate=DiagnosisCertificate(
            patient_name=_f(), diagnosis=_f(), icd_code=_f(),
            # 預設日期為合法 ISO 且已逾 30 日等待期(A2 檢核 fail-closed,非日期字串會被旗標)
            admissions=[Admission(admission_date=_f("2026-03-02"), discharge_date=_f("2026-03-07"), icu_days=_f("0"))],
            treatment_summary=_f(), issue_date=_f(), hospital_name=_f(),
        ),
        medical_receipt=MedicalReceipt(
            hospital_name=_f(), patient_name=_f(), receipt_date=_f(),
            items=[], total_amount=Decimal("0"), is_copy=False,
        ),
        claim_application=ClaimApplication(
            applicant_name=_f(), policy_number=_f(policy_number), policy_effective_date=_f("2025-01-10"),
            claim_type=_f(),
            application_date=_f("2026-03-15"), claimed_amount=Decimal("0"),  # 合法日期:P4-2 時效事實會解析
            other_insurer_paid=OtherInsurerPaid(room_fee=0, surgery_fee=0, misc_fee=0, ambulance_fee=0, unallocated=0),
        ),
        incident_statement=None,
        low_confidence_fields=low_conf or [],
    )


def _result(
    decision: ClaimDecision = ClaimDecision.APPROVE,
    amount: str = "18000",
    citations: list[PolicyCitation] | None = None,
    confidence: float = 0.9,
) -> AdjudicationResult:
    default_citation = PolicyCitation(
        policy_id="POL-A",
        clause_no="第7條",
        quote="每日給付「住院日額保險金」新臺幣貳仟元",
        relevance="住院日額給付依據",
    )
    # B1/P2:approve 預設明細須通過條款參考表 → 以 POL-A 第7條(2000/日)+ 第11條(1000/日)拆出 amount
    # (預設 18000 = 6×2000 + 6×1000,與 _bundle 的 6 日住院一致);deny/manual 為空
    def _pu(clause: str, qty: int, unit: int) -> CalcLine:
        return CalcLine(item="<項目>", clause_no=clause, kind=CalcKind.PER_UNIT, quantity=qty,
                        unit_amount=Decimal(unit), actual_amount=Decimal(0), deduction=Decimal(0), cap=Decimal(0),
                        lump_amount=Decimal(0), ratio=Decimal(1))
    calc: list[CalcLine] = []
    if decision is ClaimDecision.APPROVE:
        amt = int(Decimal(amount))
        q7, rest = (6, amt - 12000) if amt == 18000 else divmod(amt, 2000)
        calc = [_pu("第7條", q7, 2000)] + ([_pu("第11條", rest // 1000, 1000)] if rest else [])
    return AdjudicationResult(
        claim_id="T1", decision=decision, approved_amount=Decimal(amount), calculation=calc,
        citations=citations if citations is not None else [default_citation],
        reasoning="", confidence=confidence,
    )


# --- 條款索引 ---

def test_cn_to_int():
    assert [cn_to_int(x) for x in ["一", "七", "十", "十二", "二十", "三十六"]] == [1, 7, 10, 12, 20, 36]


def test_index_covers_all_policies_and_clauses():
    idx = load_policy_index()
    assert set(idx) == {"POL-A", "POL-B", "POL-C"}
    assert len([k for k in idx["POL-A"] if k.startswith("第")]) == 36
    assert len([k for k in idx["POL-B"] if k.startswith("第")]) == 30
    assert len([k for k in idx["POL-C"] if k.startswith("第")]) == 34
    assert "附錄一" in idx["POL-A"] and "附錄二" in idx["POL-A"]
    assert "貳仟元" in idx["POL-A"]["第7條"].text


# --- Gate ---

def test_gate_pass_on_valid_result():
    g = run_gate(_result(), _bundle())
    assert g.passed and g.final_decision == ClaimDecision.APPROVE and g.flags == []


def test_gate_flags_nonexistent_clause():
    bad = _result(citations=[PolicyCitation(policy_id="POL-A", clause_no="第99條", quote="", relevance="")])
    g = run_gate(bad, _bundle())
    assert not g.passed
    assert g.final_decision == ClaimDecision.MANUAL_REVIEW
    assert any(f.check == "citation_not_found" for f in g.flags)


def test_gate_flags_fabricated_quote():
    bad = _result(citations=[PolicyCitation(
        policy_id="POL-A", clause_no="第7條", quote="每日給付新臺幣伍仟元", relevance="")])
    g = run_gate(bad, _bundle())
    assert any(f.check == "citation_quote_mismatch" for f in g.flags)


def test_gate_flags_amount_on_deny():
    g = run_gate(_result(decision=ClaimDecision.DENY, amount="1000"), _bundle())
    assert any(f.check == "amount_nonzero_on_nonapprove" for f in g.flags)


def test_gate_flags_amount_exceeds_bound():
    g = run_gate(_result(amount="960000"), _bundle())  # POL-A 單段上界 953,000
    assert any(f.check == "amount_exceeds_upper_bound" for f in g.flags)


def test_gate_upper_bound_scales_with_admissions():
    """上界按住院段數參數化:同金額在單段超界、雙段不超界(C061 抓到的缺陷)。"""
    b = _bundle()
    b.diagnosis_certificate.admissions.append(Admission(admission_date=_f(), discharge_date=_f(), icu_days=_f("0")))
    g = run_gate(_result(amount="960000"), b)
    assert not any(f.check == "amount_exceeds_upper_bound" for f in g.flags)


def test_gate_flags_approve_without_citation():
    g = run_gate(_result(citations=[]), _bundle())
    assert any(f.check == "approve_without_citation" for f in g.flags)


def test_gate_routes_low_confidence_to_manual():
    g = run_gate(_result(confidence=0.5), _bundle())
    assert g.final_decision == ClaimDecision.MANUAL_REVIEW
    assert any(f.check == "low_confidence" for f in g.flags)


def test_gate_catches_unlisted_low_confidence_field():
    """Extraction Agent 漏標低信心欄位時,Gate 掃信心分數兜底(A18);含列表內巢狀路徑。"""
    b = _bundle()
    b.diagnosis_certificate.admissions[0].icu_days = _f("0", c=0.5)
    g = run_gate(_result(), b)
    flag = next(f for f in g.flags if f.check == "low_confidence_unlisted")
    assert "diagnosis_certificate.admissions[0].icu_days" in flag.detail
    assert g.final_decision == ClaimDecision.MANUAL_REVIEW


def test_gate_no_unlisted_flag_when_field_is_listed():
    b = _bundle(low_conf=["diagnosis_certificate.icd_code"])
    b.diagnosis_certificate.icd_code = _f("x", c=0.5)
    g = run_gate(_result(), b)
    assert not any(f.check == "low_confidence_unlisted" for f in g.flags)


def test_gate_low_confidence_scan_skips_absent_optional(monkeypatch: pytest.MonkeyPatch):
    """缺席 ≠ 低信心:optional 欄位為 None 時不得產生任何低信心路徑(commit 0 守門)。"""
    from app.gate import checks

    class WithOptional(ClaimBundle):
        extra_doc: ExtractedField | None = None

    b = _bundle()
    wo = WithOptional(**b.model_dump(), extra_doc=None)
    assert checks._low_confidence_paths(wo) == []
    wo2 = WithOptional(**b.model_dump(), extra_doc=_f("x", c=0.1))
    assert checks._low_confidence_paths(wo2) == ["extra_doc"]


def test_gate_flags_low_confidence_extraction_fields():
    # 2026-10-01 起以程式量測為準:欄位信心必須真的低於門檻才旗標(模型清單只作對照)
    b = _bundle(low_conf=["diagnosis_certificate.icd_code"])
    b.diagnosis_certificate.icd_code = _f("x", c=0.5)
    g = run_gate(_result(), b)
    assert any(f.check == "low_confidence_fields" for f in g.flags)


def test_gate_deny_with_zero_amount_and_valid_citation_passes():
    ok = _result(
        decision=ClaimDecision.DENY, amount="0",
        citations=[PolicyCitation(
            policy_id="POL-A", clause_no="第22條",
            quote="三十日內因疾病住院診療者,本公司不負給付保險金之責任", relevance="等待期未滿")],
    )
    g = run_gate(ok, _bundle())
    assert g.passed and g.final_decision == ClaimDecision.DENY


@pytest.mark.parametrize("raw, expected", [
    ("第7條", "第7條"), ("第七條", "第7條"), ("第 7 條", "第7條"), ("7", "第7條"),
    ("第7條第三款", "第7條"), ("第二十二條", "第22條"), ("附錄一", "附錄一"),
    ("附錄 1", "附錄一"), ("附錄2", "附錄二"), ("胡說", "胡說"),
])
def test_normalize_clause_no(raw, expected):
    assert normalize_clause_no(raw) == expected


@pytest.mark.parametrize("variant", ["第七條", "第 7 條", "7", "第7條第一項"])
def test_gate_accepts_clause_no_variants(variant):
    """LLM 各種條號寫法不得被誤判為不存在(A4)。"""
    r = _result(citations=[PolicyCitation(
        policy_id="POL-A", clause_no=variant,
        quote="每日給付「住院日額保險金」新臺幣貳仟元", relevance="")])
    g = run_gate(r, _bundle())
    assert not any(f.check == "citation_not_found" for f in g.flags), variant
    assert g.passed


def test_core_clause_rules_are_title_based_and_stable():
    """核心集只依標題規則:三張保單條數 18/14/18(2026-10-01 人工核定:初版 43 條 + 時效 5 條 +
    B 第11條未列表手術 + C 第7條給付總則),必含名詞定義、應備文件、時效與附錄一,不含除外責任。"""
    import re

    from app.gate.policy_index import CORE_TITLE_RULES, core_clause_nos, load_policy_index

    index = load_policy_index()
    sizes = {pol: len(core_clause_nos(cl)) for pol, cl in index.items()}
    assert sizes == {"POL-A": 18, "POL-B": 14, "POL-C": 18}
    for pol, cl in index.items():
        core = set(core_clause_nos(cl))
        assert "第2條" in core and "附錄一" in core
        titles = {index[pol][c].title for c in core}
        assert "應備文件" in titles and "時效" in titles
        assert not any("除外" in t for t in titles), "除外責任不屬核心集"
    assert "第11條" in core_clause_nos(index["POL-B"]) and "第7條" in core_clause_nos(index["POL-C"])
    # 類別優先序:「保險金之給付期限」歸程序,不歸給付
    cat = next(k for k, pat in CORE_TITLE_RULES if re.search(pat, "保險金之給付期限"))
    assert cat == "程序"



def test_citation_policy_id_accepts_policy_number_and_bare_clause_no():
    """2026-10-01 C008 真實觸發:模型把 policy_id 填成保單號碼 POL-A-2026-01、clause_no 填 7,
    Gate 應正規化為 POL-A / 第7條 後反查成立(條號正規化已有,保單代號正規化補齊)。"""
    from app.gate.checks import validate_citation
    from app.gate.policy_index import load_policy_index, normalize_policy_id

    index = load_policy_index()
    assert normalize_policy_id("POL-A-2026-01") == "POL-A"
    assert normalize_policy_id(" POL-C ") == "POL-C"
    assert normalize_policy_id("XYZ-1") == "XYZ-1"
    quote = index["POL-A"]["第7條"].text[:15]
    cit = PolicyCitation(policy_id="POL-A-2026-01", clause_no="7", quote=quote, relevance="t")
    assert validate_citation(cit, index) is None
    bad = PolicyCitation(policy_id="POL-Z-2026-01", clause_no="7", quote=quote, relevance="t")
    assert validate_citation(bad, index) is not None


def test_extraction_threshold_is_the_grid_search_plateau_value():
    """2026-10-01 使用者定案:0.65(格搜高原上緣);改動屬 guardrail 生產變更,須重跑 72 案格搜並記錄理由。"""
    from app.gate.checks import CONFIDENCE_THRESHOLD, EXTRACTION_FIELD_CONFIDENCE_THRESHOLD
    assert EXTRACTION_FIELD_CONFIDENCE_THRESHOLD == 0.65 and CONFIDENCE_THRESHOLD == 0.70


# --- 引用正規化(SPEC 2026-10-01-b C;72 案首跑 63 筆不符中 46 筆僅差標點/markdown 粗體)---
def test_citation_matches_despite_punctuation_and_markdown_differences():
    # 第11條原文該處以「,」接續,模型引文以「。」收尾;第8條原文含 **粗體** 標記,模型引文無
    cases = [
        ("第11條", "POL-A",
         "被保險人依第七條約定住院診療者,本公司按其實際住院日數,每日給付「住院醫療輔助保險金」新臺幣壹仟元。"),
        ("第8條", "POL-C", "於每日新臺幣參仟元之限額內給付「病房費用保險金」。被保險人自願升等病房"),
        ("第2條", "POL-A",
         "二、「住院」:係指被保險人經醫師診斷其疾病或傷害必須入住醫院,且正式辦理住院手續並確實在醫院接受診療者"),
    ]
    for clause_no, pol, quote in cases:
        r = _result(citations=[PolicyCitation(policy_id=pol, clause_no=clause_no, quote=quote, relevance="")])
        g = run_gate(r, _bundle(f"{pol}-2026-01"))
        assert not any(f.check == "citation_quote_mismatch" for f in g.flags), (clause_no, g.flags)


def test_citation_paraphrase_still_mismatches_after_normalization():
    # 去標點不得讓改寫/摘要句矇混:附錄一無此句;數字改寫亦不得相符
    for pol, clause_no, quote in [
        ("POL-C", "附錄一", "骨折開放復位內固定術屬於第二級手術。"),
        ("POL-A", "第7條", "每日給付「住院日額保險金」新臺幣伍仟元"),
    ]:
        r = _result(citations=[PolicyCitation(policy_id=pol, clause_no=clause_no, quote=quote, relevance="")])
        g = run_gate(r, _bundle(f"{pol}-2026-01"))
        assert any(f.check == "citation_quote_mismatch" for f in g.flags), (clause_no, quote)


# --- 等待期確定性檢核(SPEC 2026-10-01-b A2;72 案首跑 8 案 deny→approve 中 3 案為 30 日等待期未滿)---
def _wp_bundle(effective: str, admission: str | None, icd: str, receipt: str = "2026-05-15") -> ClaimBundle:
    b = _bundle()
    dc = b.diagnosis_certificate
    adms = ([Admission(admission_date=_f(admission), discharge_date=_f(admission), icu_days=_f("0"))]
            if admission else [])
    dc = dc.model_copy(update={"admissions": adms, "icd_code": _f(icd)})
    app = b.claim_application.model_copy(update={"policy_effective_date": _f(effective)})
    rc = b.medical_receipt.model_copy(update={"receipt_date": _f(receipt)})
    return b.model_copy(update={"diagnosis_certificate": dc, "claim_application": app, "medical_receipt": rc})


def _wp_flags(bundle: ClaimBundle, decision: ClaimDecision = ClaimDecision.APPROVE) -> list[str]:
    amount = "18000" if decision is ClaimDecision.APPROVE else "0"
    return [f.check for f in run_gate(_result(decision=decision, amount=amount), bundle).flags]


def test_waiting_period_constant_is_thirty_days():
    from app.gate.checks import WAITING_PERIOD_DAYS
    assert WAITING_PERIOD_DAYS == 30


@pytest.mark.parametrize("effective, admission, icd", [
    ("2026-06-20", "2026-07-05", "K81.0"),  # C021 形:15 日
    ("2026-04-01", "2026-04-20", "K35.8"),  # C056 形:19 日
    ("2026-05-01", "2026-05-25", "K52.9"),  # C057 形:24 日
    ("2026-05-01", "2026-05-30", "K52.9"),  # 邊界:29 日(<30)
    ("2026-05-01", "2026-04-20", "K52.9"),  # A2-6:事件日早於生效日
])
def test_waiting_period_unmet_disease_approve_is_flagged(effective, admission, icd):
    flags = _wp_flags(_wp_bundle(effective, admission, icd))
    assert "waiting_period_unmet" in flags
    g = run_gate(_result(), _wp_bundle(effective, admission, icd))
    assert g.final_decision is ClaimDecision.DENY  # SPEC-p P2(2026-10-03):deny 類旗標改派 deny,不再人工(ADR-008)


@pytest.mark.parametrize("effective, admission, icd", [
    ("2026-05-01", "2026-05-31", "K52.9"),  # 邊界:恰 30 日(不小於 30)→ 不旗標
    ("2025-01-10", "2026-03-02", "J18.9"),  # C001 形:早已逾等待期
    ("2026-05-01", "2026-05-12", "S52.501"),  # A2-3 C048 形:傷害(ICD S)11 日
    ("2026-06-01", "2026-06-09", "S52.201"),  # A2-3 C063 形:傷害 8 日
    ("2026-06-01", "2026-06-09", "T14.1"),  # ICD T 章亦屬傷害
])
def test_waiting_period_not_flagged_when_met_or_injury(effective, admission, icd):
    assert "waiting_period_unmet" not in _wp_flags(_wp_bundle(effective, admission, icd))
    assert "waiting_period_unverifiable" not in _wp_flags(_wp_bundle(effective, admission, icd))


def test_waiting_period_uses_receipt_date_when_no_admission():
    # A2-1 門診手術無住院段:事件日取收據日
    assert "waiting_period_unmet" in _wp_flags(_wp_bundle("2026-05-01", None, "D17.9", receipt="2026-05-10"))
    assert "waiting_period_unmet" not in _wp_flags(_wp_bundle("2025-01-15", None, "D17.9", receipt="2026-05-10"))


@pytest.mark.parametrize("effective, admission",
                         [("x", "2026-05-20"), ("2026-05-01", "2026/05/20"), ("", "2026-05-20")])
def test_waiting_period_unparseable_dates_fail_closed(effective, admission):
    flags = _wp_flags(_wp_bundle(effective, admission, "K52.9"))
    assert "waiting_period_unverifiable" in flags and "waiting_period_unmet" not in flags


def test_waiting_period_not_checked_on_deny_or_manual():
    b = _wp_bundle("x", "2026-05-20", "K52.9")  # 連日期都壞,非 approve 仍不得旗標
    for d in (ClaimDecision.DENY, ClaimDecision.MANUAL_REVIEW):
        flags = _wp_flags(b, d)
        assert "waiting_period_unmet" not in flags and "waiting_period_unverifiable" not in flags


# --- B1-4 核付必附計算明細(SPEC 2026-10-01-b)---
def test_gate_flags_approve_without_calculation():
    r = _result().model_copy(update={"calculation": []})
    g = run_gate(r, _bundle())
    assert any(f.check == "calculation_empty_on_approve" for f in g.flags)
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_gate_does_not_require_calculation_on_deny_or_manual():
    for d in (ClaimDecision.DENY, ClaimDecision.MANUAL_REVIEW):
        r = _result(decision=d, amount="0").model_copy(update={"calculation": []})
        assert not any(f.check == "calculation_empty_on_approve" for f in run_gate(r, _bundle()).flags)


# --- J1 核付明細不得含算出 0 的行(v8 C055:病房費 capped_per_unit 9 × 3,000 但 actual 0 → 整行歸零,少付 22,000)---
def test_j1_zero_value_line_on_approve_is_flagged():
    zero = _line_pu("第8條", 9, 3000, CalcKind.CAPPED_PER_UNIT, actual=0)
    ok = _line_pu("第8條", 9, 3000, CalcKind.CAPPED_PER_UNIT, actual=22000)
    r = _result().model_copy(update={"calculation": [ok, zero]})
    g = run_gate(r, _bundle("POL-C-2026-01"))
    assert [f.check for f in g.flags if f.check == "calc_zero_line"] == ["calc_zero_line"]
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW
    ok_only = _result().model_copy(update={"calculation": [ok]})
    assert not any(f.check == "calc_zero_line" for f in run_gate(ok_only, _bundle("POL-C-2026-01")).flags)


def test_j1_zero_line_not_checked_on_deny_or_manual():
    zero = _line_pu("第8條", 9, 3000, CalcKind.CAPPED_PER_UNIT, actual=0)
    for d in (ClaimDecision.DENY, ClaimDecision.MANUAL_REVIEW):
        r = _result(decision=d, amount="0").model_copy(update={"calculation": [zero]})
        flags = run_gate(r, _bundle("POL-C-2026-01")).flags
        assert not any(f.check == "calc_zero_line" for f in flags)


# --- P2 逐行反查接入 Gate(SPEC 2026-10-01-c)---
def test_gate_flags_calculation_reference_mismatch_and_routes_manual():
    bad_line = CalcLine(item="日額", clause_no="第7條", kind=CalcKind.PER_UNIT, quantity=6, unit_amount=Decimal(2500),
                        actual_amount=Decimal(0), deduction=Decimal(0), cap=Decimal(0), lump_amount=Decimal(0),
                        ratio=Decimal(1))
    r = _result(amount="15000").model_copy(update={"calculation": [bad_line]})
    g = run_gate(r, _bundle())
    assert any(f.check == "calc_reference_mismatch" and "第7條" in f.detail for f in g.flags)
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_gate_reference_check_uses_policy_from_application_and_skips_nonapprove():
    good = CalcLine(item="日額", clause_no="第7條", kind=CalcKind.PER_UNIT, quantity=6, unit_amount=Decimal(2000),
                    actual_amount=Decimal(0), deduction=Decimal(0), cap=Decimal(0), lump_amount=Decimal(0),
                    ratio=Decimal(1))
    r = _result(amount="12000").model_copy(update={"calculation": [good]})
    assert not any(f.check in ("calc_reference_mismatch", "calc_kind_mismatch", "calc_unknown_clause",
                               "calc_ratio_invalid") for f in run_gate(r, _bundle()).flags)
    # 同一行套到 POL-C(第7條為給付總則,表外)→ unknown;deny 時不檢
    assert any(f.check == "calc_unknown_clause" for f in run_gate(r, _bundle("POL-C-2026-01")).flags)
    d = _result(decision=ClaimDecision.DENY, amount="0").model_copy(update={"calculation": [bad := good]})
    assert not any(f.check.startswith("calc_") for f in run_gate(d, _bundle("POL-C-2026-01")).flags)
    del bad


# --- P3 日數檢核(SPEC 2026-10-01-c;依 derived_facts 候選日數集)---
def _days_bundle(adms: list[tuple[str, str, str]], policy_number: str = "POL-A-2026-01") -> ClaimBundle:
    b = _bundle(policy_number)
    dc = b.diagnosis_certificate.model_copy(update={
        "admissions": [Admission(admission_date=_f(a), discharge_date=_f(d), icu_days=_f(i)) for a, d, i in adms]})
    return b.model_copy(update={"diagnosis_certificate": dc})


def _line_pu(clause: str, qty: int, unit: int, kind: CalcKind = CalcKind.PER_UNIT, actual: int = 0) -> CalcLine:
    return CalcLine(item="<項目>", clause_no=clause, kind=kind, quantity=qty, unit_amount=Decimal(unit),
                    actual_amount=Decimal(actual), deduction=Decimal(0), cap=Decimal(0), lump_amount=Decimal(0),
                    ratio=Decimal(1))


def _days_flags(bundle: ClaimBundle, lines: list[CalcLine]) -> list[str]:
    from app.gate.calc import total_amount
    r = _result(amount=str(total_amount(lines))).model_copy(update={"calculation": lines})
    return [f.check for f in run_gate(r, bundle).flags if f.check == "calc_days_mismatch"]


C001 = [("2026-03-02", "2026-03-07", "0")]  # 6 日
C008 = [("2026-01-05", "2026-02-24", "0"), ("2026-03-06", "2026-04-20", "0")]  # 51 + 46 = 97 → 上限 90
C060 = [("2026-01-05", "2026-03-05", "0"), ("2026-03-20", "2026-05-03", "0")]  # 60 + 45 = 105(不合併)
C019 = [("2026-05-01", "2026-05-20", "7")]  # 20 日,ICU 7
UNIT = {"第7條": 2000, "第8條": 4000, "第11條": 1000}


@pytest.mark.parametrize("adms, clause, qty", [
    (C001, "第7條", 6), (C001, "第11條", 6),
    (C008, "第7條", 90), (C008, "第11條", 90), (C008, "第7條", 51),  # 97(合併未截斷)自 SPEC-g G1 起排除
    (C060, "第7條", 60), (C060, "第7條", 45), (C060, "第7條", 105),  # 90(合併截斷)自 SPEC-d S4 起排除:間隔 15 日
    (C019, "第7條", 13), (C019, "第7條", 20), (C019, "第8條", 7), (C019, "第11條", 20),
])
def test_day_quantities_within_candidate_set_pass(adms, clause, qty):
    assert _days_flags(_days_bundle(adms), [_line_pu(clause, qty, UNIT[clause])]) == []


@pytest.mark.parametrize("adms, clause, qty", [
    (C001, "第7條", 5),  # 不含出院日(v2 ≥ 8 案)
    (C001, "第11條", 7),
    (C008, "第7條", 100),
    (C060, "第7條", 120),  # C060 v2:未截 90 亦非兩段各算
    (C019, "第8條", 8),  # ICU 日數非 7
    (C019, "第7條", 14),
])
def test_day_quantities_outside_candidate_set_flagged(adms, clause, qty):
    flags = _days_flags(_days_bundle(adms), [_line_pu(clause, qty, UNIT[clause])])
    assert flags == ["calc_days_mismatch"]


def test_pol_b_solace_days_capped_at_thirty_and_pol_c_room_days():
    b37 = _days_bundle([("2026-05-25", "2026-06-30", "0")], "POL-B-2026-01")  # 37 日 → 慰問 30
    assert _days_flags(b37, [_line_pu("第12條", 30, 1000)]) == []
    assert _days_flags(b37, [_line_pu("第12條", 37, 1000)]) == ["calc_days_mismatch"]
    c10 = _days_bundle([("2026-04-01", "2026-04-10", "0")], "POL-C-2026-01")  # 10 日
    assert _days_flags(c10, [_line_pu("第8條", 10, 3000, CalcKind.CAPPED_PER_UNIT, actual=35000)]) == []
    bad = _days_flags(c10, [_line_pu("第8條", 9, 3000, CalcKind.CAPPED_PER_UNIT, actual=35000)])
    assert bad == ["calc_days_mismatch"]


def test_day_check_skips_non_day_clauses_and_unparseable_dates():
    # 第10條手術定額非日數行 → 不檢;日期壞掉 → 候選集為空 → 不檢(A2 已 fail-closed)
    lump = CalcLine(item="手術", clause_no="第10條", kind=CalcKind.LUMP_SUM, quantity=0, unit_amount=Decimal(0),
                    actual_amount=Decimal(0), deduction=Decimal(0), cap=Decimal(0), lump_amount=Decimal(25000),
                    ratio=Decimal(1))
    assert _days_flags(_days_bundle(C001), [lump]) == []
    assert _days_flags(_days_bundle([("x", "2026-03-07", "0")]), [_line_pu("第7條", 5, 2000)]) == []


def test_calculation_checks_skip_when_policy_number_cannot_be_resolved():
    # 保單無法歸戶:P2/P3 不檢(由 amount 檢核的 unknown_policy_number 轉人工),避免重複旗標
    flags = [f.check for f in run_gate(_result(), _bundle("ZZZ-0000")).flags]
    assert "unknown_policy_number" in flags and not any(c.startswith("calc_") for c in flags)


# --- P4-1 結構檢核(SPEC 2026-10-01-d;v3:漏第11條 ×4、兩項手術、年度 195 日、間隔 15 日仍合併)---
def _lump(clause: str, amount: int, ratio: str = "1") -> CalcLine:
    return CalcLine(item="手術", clause_no=clause, kind=CalcKind.LUMP_SUM, quantity=0, unit_amount=Decimal(0),
                    actual_amount=Decimal(0), deduction=Decimal(0), cap=Decimal(0), lump_amount=Decimal(amount),
                    ratio=Decimal(ratio))


def _flags_of(bundle: ClaimBundle, lines: list[CalcLine], prefix: str) -> list[str]:
    from app.gate.calc import total_amount
    r = _result(amount=str(total_amount(lines))).model_copy(update={"calculation": lines})
    return sorted({f.check for f in run_gate(r, bundle).flags if f.check.startswith(prefix)})


C061 = [("2026-01-05", "2026-03-15", "0"), ("2026-04-24", "2026-06-22", "0"),
        ("2026-08-22", "2026-10-25", "0")]  # 70 / 60 / 65 日
C066 = [("2026-04-01", "2026-04-05", "0"), ("2026-04-19", "2026-04-21", "0")]  # 5 + 3,間隔恰 14 日
C054 = [("2026-05-01", "2026-05-05", "0")]  # 單段 5 日


def test_s1_pol_a_aux_line_must_pair_with_daily_lines():
    b = _days_bundle(C001)
    assert _flags_of(b, [_line_pu("第7條", 6, 2000), _line_pu("第11條", 6, 1000)], "calc_structure") == []
    assert _flags_of(b, [_line_pu("第7條", 6, 2000)], "calc_structure") == ["calc_structure_mismatch"]  # C011 形
    assert _flags_of(b, [_line_pu("第11條", 6, 1000)], "calc_structure") == ["calc_structure_mismatch"]  # 反向
    mism = _flags_of(b, [_line_pu("第7條", 6, 2000), _line_pu("第11條", 5, 1000)], "calc_structure")
    assert mism == ["calc_structure_mismatch"]


def test_s1_aux_quantity_equals_ward_plus_icu():
    b = _days_bundle(C019)  # 20 日,ICU 7:輔助 20 = 日額 13 + 加護 7(真值 C019)
    good = [_line_pu("第7條", 13, 2000), _line_pu("第8條", 7, 4000), _line_pu("第11條", 20, 1000)]
    assert _flags_of(b, good, "calc_structure") == []
    bad = [_line_pu("第7條", 13, 2000), _line_pu("第8條", 7, 4000), _line_pu("第11條", 13, 1000)]
    assert _flags_of(b, bad, "calc_structure") == ["calc_structure_mismatch"]


def test_s2_pol_a_annual_cap_180():
    b = _days_bundle(C061)
    over = [_line_pu("第7條", q, 2000) for q in (70, 60, 65)] + [_line_pu("第11條", q, 1000) for q in (70, 60, 65)]
    assert _flags_of(b, over, "calc_structure") == ["calc_structure_mismatch"]  # 195 > 180(C061 v3)
    ok = [_line_pu("第7條", q, 2000) for q in (70, 60, 50)] + [_line_pu("第11條", q, 1000) for q in (70, 60, 50)]
    assert _flags_of(b, ok, "calc_structure") == []  # 180 恰等於上限
    two = _days_bundle(C060)
    assert _flags_of(two, [_line_pu("第7條", 60, 2000), _line_pu("第7條", 45, 2000),
                           _line_pu("第11條", 105, 1000)], "calc_structure") == []


def test_s3_at_most_one_surgery_lump_per_admission():
    one = _days_bundle(C054)
    base = [_line_pu("第7條", 5, 2000), _line_pu("第11條", 5, 1000)]
    assert _flags_of(one, base + [_lump("附錄一", 25000)], "calc_structure") == []
    two_lumps = _flags_of(one, base + [_lump("附錄一", 25000), _lump("附錄一", 10000)], "calc_structure")
    assert two_lumps == ["calc_structure_mismatch"]
    three = _days_bundle(C061)
    base3 = [_line_pu("第7條", 60, 2000), _line_pu("第11條", 60, 1000)]
    assert _flags_of(three, base3 + [_lump("第10條", 25000)] * 3, "calc_structure") == []
    assert _flags_of(three, base3 + [_lump("第10條", 25000)] * 4, "calc_structure") == ["calc_structure_mismatch"]
    # POL-B:ratio=1 的手術行 ≤ 段數;次高 0.5 行不計(F2 後住院手術需配第12條,fixture 補上)
    b = _days_bundle(C054, "POL-B-2026-01")
    solace = _line_pu("第12條", 5, 1000)
    assert _flags_of(b, [_lump("第7條", 30000), _lump("第9條", 15000, "0.5"), solace], "calc_structure") == []
    two_full = _flags_of(b, [_lump("第7條", 30000), _lump("第7條", 15000), solace], "calc_structure")
    assert two_full == ["calc_structure_mismatch"]


def test_s4_merged_total_excluded_when_all_gaps_exceed_fourteen():
    b60 = _days_bundle(C060)  # 間隔 15 → 不得合併截 90
    merged = _flags_of(b60, [_line_pu("第7條", 90, 2000), _line_pu("第11條", 90, 1000)], "calc_days")
    assert merged == ["calc_days_mismatch"]
    assert _flags_of(b60, [_line_pu("第7條", 60, 2000), _line_pu("第7條", 45, 2000),
                           _line_pu("第11條", 105, 1000)], "calc_days") == []
    b66 = _days_bundle(C066)  # 間隔恰 14 → 「十四日內」含 14,可合併
    assert _flags_of(b66, [_line_pu("第7條", 8, 2000), _line_pu("第11條", 8, 1000)], "calc_days") == []
    # 間隔恰 14 且合計逾 90:合併後截 90 必須是合法形狀(只有此形狀能分辨「含第 14 日」與「不含」;變異 M21)
    b14 = _days_bundle([("2026-01-05", "2026-03-05", "0"), ("2026-03-19", "2026-05-02", "0")])  # 60 + 45,gap 14
    assert _flags_of(b14, [_line_pu("第7條", 90, 2000), _line_pu("第11條", 90, 1000)], "calc_days") == []
    b08 = _days_bundle(C008)  # 間隔 10 → 合併截 90 仍合法
    assert _flags_of(b08, [_line_pu("第7條", 90, 2000), _line_pu("第11條", 90, 1000)], "calc_days") == []
    # POL-B 慰問金無合併條款:不排除
    bb = _days_bundle(C060, "POL-B-2026-01")
    assert _flags_of(bb, [_lump("第7條", 30000), _line_pu("第12條", 30, 1000)], "calc_days") == []


def test_s5_structure_checks_skip_nonapprove_and_unparseable():
    deny = _result(decision=ClaimDecision.DENY, amount="0").model_copy(
        update={"calculation": [_line_pu("第7條", 6, 2000)]})
    assert not any(f.check.startswith("calc_structure") for f in run_gate(deny, _days_bundle(C001)).flags)
    broken = _days_bundle([("x", "2026-03-07", "0")])  # 配對與 180 上限皆不靠日期,日期壞掉仍檢
    assert _flags_of(broken, [_line_pu("第7條", 6, 2000)], "calc_structure") == ["calc_structure_mismatch"]
    over = _flags_of(broken, [_line_pu("第7條", 200, 2000), _line_pu("第11條", 200, 1000)], "calc_structure")
    assert over == ["calc_structure_mismatch"]


# --- P4-2 時效 / P4-3 特定疾病窗口(SPEC 2026-10-01-d)---
def _dated(adms, effective: str, app_date: str, icd: str = "K52.9", policy_number: str = "POL-A-2026-01",
           receipt: str | None = None) -> ClaimBundle:
    b = _days_bundle(adms, policy_number)
    dc = b.diagnosis_certificate.model_copy(update={"icd_code": _f(icd)})
    app = b.claim_application.model_copy(update={"policy_effective_date": _f(effective),
                                                 "application_date": _f(app_date)})
    rc = b.medical_receipt.model_copy(update={"receipt_date": _f(receipt or (adms[-1][1] if adms else app_date))})
    return b.model_copy(update={"diagnosis_certificate": dc, "claim_application": app, "medical_receipt": rc})


def _checks(bundle: ClaimBundle, decision: ClaimDecision = ClaimDecision.APPROVE) -> list[str]:
    amount = "18000" if decision is ClaimDecision.APPROVE else "0"
    return [f.check for f in run_gate(_result(decision=decision, amount=amount), bundle).flags]


@pytest.mark.parametrize("discharge, app_date, flagged", [
    ("2024-04-10", "2026-05-02", True),   # C067:752 日
    ("2024-04-10", "2026-04-10", False),  # 恰滿兩年當日:不逾
    ("2024-04-10", "2026-04-11", True),   # 翌日:逾
    ("2024-02-29", "2026-02-28", False),  # 閏日起算:視為 2026-02-28 到期
    ("2024-02-29", "2026-03-01", True),
    ("2026-03-07", "2026-03-15", False),
])
def test_claim_deadline_two_years_from_event(discharge, app_date, flagged):
    b = _dated([("2024-01-01" if discharge < "2026" else "2026-03-02", discharge, "0")], "2023-01-10", app_date)
    assert ("claim_deadline_exceeded" in _checks(b)) is flagged


def test_claim_deadline_skips_nonapprove_and_unparseable():
    b = _dated([("2024-04-06", "2024-04-10", "0")], "2023-01-10", "2026-05-02")
    assert "claim_deadline_exceeded" not in _checks(b, ClaimDecision.DENY)
    assert "claim_deadline_exceeded" not in _checks(_dated([("2024-04-06", "2024-04-10", "0")], "2023-01-10", "x"))


@pytest.mark.parametrize("effective, admission, icd, policy, flagged", [
    ("2026-03-10", "2026-05-20", "I21.9", "POL-A-2026-01", True),    # C022:71 日,疾病
    ("2026-03-01", "2026-05-10", "K80.00", "POL-A-2026-01", False),  # C049:SPEC-ag 非附錄二(第24條檢核)
    ("2025-01-10", "2026-03-02", "J18.9", "POL-A-2026-01", False),   # 早已逾 90
    ("2026-03-01", "2026-05-30", "J18.9", "POL-A-2026-01", False),   # 恰 90 日:不旗標
    ("2026-03-01", "2026-05-29", "I63.9", "POL-A-2026-01", True),    # 89 日(SPEC-ag:改用附錄二 ICD)
    ("2026-03-10", "2026-05-20", "S52.501", "POL-A-2026-01", False), # 傷害豁免
    ("2026-03-10", "2026-05-20", "K35.8", "POL-B-2026-01", False),   # POL-B 無延長等待期
    ("2026-03-10", "2026-05-20", "K35.8", "POL-C-2026-01", False),
    ("2026-05-01", "2026-05-25", "I21.9", "POL-A-2026-01", False),   # <30 由 A2 負責(SPEC-ag 改附錄二碼)
])
def test_special_waiting_period_window_pol_a_30_to_89_days(effective, admission, icd, policy, flagged):
    b = _dated([(admission, admission, "0")], effective, "2026-06-30", icd, policy)
    flags = _checks(b)
    assert ("special_waiting_period_review" in flags) is flagged
    if effective == "2026-05-01":
        assert "waiting_period_unmet" in flags


def test_special_waiting_period_skips_nonapprove():
    b = _dated([("2026-05-20", "2026-05-25", "0")], "2026-03-10", "2026-06-30", "I21.9")
    assert "special_waiting_period_review" not in _checks(b, ClaimDecision.MANUAL_REVIEW)


def test_s3_pol_b_halved_second_surgery_line_not_counted_as_full():
    b = _days_bundle(C054, "POL-B-2026-01")  # C051 形:30000×1 + 7500×1(次高手術折半表示)+ 慰問金(F2)
    lines = [_lump("第7條", 30000), _lump("附錄一", 7500), _line_pu("第12條", 5, 1000)]
    assert _flags_of(b, lines, "calc_structure") == []


# --- 信心分流改以程式量測為準(2026-10-01:runner 從未把門檻值注入 prompt,
#     模型自列的 low_confidence_fields 不可作為分流依據)---
def test_routing_ignores_model_listed_fields_that_are_actually_confident():
    # 模型把一個信心 0.95 的欄位列進 low_confidence_fields → 不得因此轉人工
    b = _bundle(low_conf=["diagnosis_certificate.diagnosis"])
    g = run_gate(_result(), b)
    assert not any(f.check in ("low_confidence_fields", "low_confidence_unlisted") for f in g.flags)
    assert g.final_decision is ClaimDecision.APPROVE


def test_routing_flags_program_measured_low_confidence_even_if_model_listed_it():
    from app.gate.checks import EXTRACTION_FIELD_CONFIDENCE_THRESHOLD as T
    b = _bundle(low_conf=["diagnosis_certificate.treatment_summary"])
    dc = b.diagnosis_certificate.model_copy(update={"treatment_summary": _f("x", T - 0.01)})
    g = run_gate(_result(), b.model_copy(update={"diagnosis_certificate": dc}))
    checks_ = [f.check for f in g.flags]
    assert "low_confidence_fields" in checks_ and "low_confidence_unlisted" not in checks_
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_extraction_system_prompt_carries_numeric_threshold(tmp_path: Path):
    from app.gate.checks import EXTRACTION_FIELD_CONFIDENCE_THRESHOLD
    from app.pipeline import runner
    from tests.test_pipeline import FakeAOAI, FakeDI, _bundle_payload, _case, _decision_payload
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, load_policy_index(), tmp_path)
    assert f"EXTRACTION_FIELD_CONFIDENCE_THRESHOLD = {EXTRACTION_FIELD_CONFIDENCE_THRESHOLD}" in aoai.calls[0]["system"]


# --- SPEC 2026-10-01-e:引用片段規則 + 條號誤植降級(v3 20 案 26 筆不符:A 11 / B 2 / C 13)---
def _cit_flags(pol: str, clause_no: str, quote: str) -> list[str]:
    r = _result(citations=[PolicyCitation(policy_id=pol, clause_no=clause_no, quote=quote, relevance="")])
    return [f.check for f in run_gate(r, _bundle(f"{pol}-2026-01")).flags if f.check.startswith("citation")]


@pytest.mark.parametrize("pol, clause_no, quote", [
    # A:省略號跨段(第17條)
    ("POL-C", "第17條",
     "被保險人因飲酒後駕(騎)車,其吐氣或血液所含酒精成分超過道路交通法令規定標準...所致之傷害,本公司不負給付保險金之責任"),
    # A:markdown 表格列
    ("POL-A", "附錄一", "| 第二級 | 骨折開放復位內固定術"),
    # A:句號拼接兩個完整句(第10條首段 + 末段)
    ("POL-A", "第10條",
     "被保險人於住院期間因疾病或傷害經醫師診斷必須接受手術者。同一次住院接受二項以上手術者,以給付金額最高之一項為限。"),
])
def test_e1_ordered_fragments_all_verbatim_in_clause_match(pol, clause_no, quote):
    assert _cit_flags(pol, clause_no, quote) == []


@pytest.mark.parametrize("pol, clause_no, quote", [
    ("POL-A", "第7條", "每日給付新臺幣伍仟元"),  # 捏造金額
    ("POL-A", "第22條", "被保險人自本契約生效日起六十日內因疾病住院診療者"),  # 捏造日數
    ("POL-A", "第10條", "第二級手術:新臺幣伍萬元"),  # 等級/金額重組(RED 揭露「:」不可為分隔符)
    ("POL-A", "第22條", "被保險人自本契約生效日起三十日內因疾病住院診療者,本公司不負給付責任。"),  # 刪字改寫(C013)
    ("POL-A", "第10條", "手術保險金。被保險人於住院期間"),  # 同條片段錯序
    ("POL-A", "第10條", "被保險人...壹萬元...伍萬元...貳萬伍仟元"),  # 錯序短片段(RED 揭露短片段不得丟棄)
    ("POL-A", "第2條", "本契約所用名詞定義如下:「住院」:係指被保險人"),  # 無標記跳款拼接(「:」不切)
    ("POL-A", "第10條", "給付「手術保險金」:第二級手術:新臺幣貳萬伍仟元。"),  # 跳項目標記拼接:修訂後歸 C 類
])
def test_e1_fabricated_or_reordered_or_rewritten_still_mismatch(pol, clause_no, quote):
    assert "citation_quote_mismatch" in _cit_flags(pol, clause_no, quote)


def test_e1_two_char_fragments_count_and_punctuation_residue_ignored():
    assert "citation_quote_mismatch" in _cit_flags("POL-A", "第10條", "第九級手術:新臺幣玖萬元")
    assert _cit_flags("POL-A", "第10條", "二、第二級手術:新臺幣貳萬伍仟元。") == []
    assert _cit_flags("POL-A", "第10條", "。。|…") == []  # 只剩標點:正規化後為空,視同未引用(既有行為)


def test_e2_verbatim_in_other_clause_is_misattribution_not_routing():
    # B:引第10條的「第一級手術:新臺幣壹萬元。」卻掛在附錄一
    cit = PolicyCitation(policy_id="POL-A", clause_no="附錄一", quote="第一級手術:新臺幣壹萬元。", relevance="")
    g = run_gate(_result(citations=[cit]), _bundle())
    flags = [f for f in g.flags if f.check.startswith("citation")]
    assert [f.check for f in flags] == ["citation_clause_misattributed"] and "第10條" in flags[0].detail
    assert g.final_decision is ClaimDecision.APPROVE and g.passed is False  # 有旗標但不改派


def test_e2_misattribution_requires_same_policy_and_still_counts_invalid_for_precision():
    from app.gate.checks import validate_citation
    c = PolicyCitation(policy_id="POL-A", clause_no="附錄一", quote="第一級手術:新臺幣壹萬元。", relevance="")
    assert validate_citation(c, load_policy_index()) is not None  # eval precision 不把誤植算 valid
    # 他保單的原文不算(POL-B 第7條金額句掛在 POL-A)→ 真不符
    assert "citation_quote_mismatch" in _cit_flags("POL-A", "第10條", "五、第五級手術:新臺幣壹拾萬元。")


# --- SPEC 2026-10-02-f:F1 限額合計 / F2 POL-B 慰問金配對 / F3 判定與推理矛盾 ---
def _cap_line(clause: str, actual: int, cap: int) -> CalcLine:
    return CalcLine(item="<項目>", clause_no=clause, kind=CalcKind.CAPPED_ACTUAL, quantity=0, unit_amount=Decimal(0),
                    actual_amount=Decimal(actual), deduction=Decimal(0), cap=Decimal(cap), lump_amount=Decimal(0),
                    ratio=Decimal(1))


def test_f1_split_lines_on_same_capped_clause_may_not_exceed_cap():
    one = _days_bundle(C054, "POL-C-2026-01")  # 單段
    # C003 形:手術費 18000 + 麻醉費 4000 各自套 20000 → 合計 22000 > 20000
    split = _flags_of(one, [_cap_line("第9條", 18000, 20000), _cap_line("第9條", 4000, 20000)], "calc_cap")
    assert split == ["calc_cap_sum_exceeded"]
    # C036 形:雜費兩行合計 ≤ 60000 → 通過
    assert _flags_of(one, [_cap_line("第10條", 18700, 60000), _cap_line("第10條", 11000, 60000)], "calc_cap") == []
    # 單行本身超限由 min() 截斷,不旗標
    assert _flags_of(one, [_cap_line("第10條", 71200, 60000)], "calc_cap") == []


def test_i1_cap_sum_compares_pre_ratio_amounts():
    # v7 C069:同 C003 形但 ratio 0.65 → ratio 後 14,300 < 20,000 曾遮蔽超限;必須以 ratio 前 22,000 比
    one = _days_bundle(C054, "POL-C-2026-01")
    lines = [_cap_line("第9條", 18000, 20000).model_copy(update={"ratio": Decimal("0.65")}),
             _cap_line("第9條", 4000, 20000).model_copy(update={"ratio": Decimal("0.65")})]
    assert _flags_of(one, lines, "calc_cap") == ["calc_cap_sum_exceeded"]
    ok = [_cap_line("第9條", 12000, 20000).model_copy(update={"ratio": Decimal("0.65")}),
          _cap_line("第9條", 4000, 20000).model_copy(update={"ratio": Decimal("0.65")})]
    assert _flags_of(one, ok, "calc_cap") == []  # 合計 16,000 ≤ 20,000,比例不影響


def test_f1_multi_admission_allows_cap_times_segments():
    two = _days_bundle(C060, "POL-C-2026-01")
    assert _flags_of(two, [_cap_line("第10條", 60000, 60000), _cap_line("第10條", 60000, 60000)], "calc_cap") == []
    assert _flags_of(two, [_cap_line("第10條", 60000, 60000)] * 3, "calc_cap") == ["calc_cap_sum_exceeded"]


def test_f1_not_applied_to_per_day_cap_or_nonapprove():
    two = _days_bundle(C060, "POL-C-2026-01")  # 第8條逐日限額兩段各一行(C055 形)
    lines = [_line_pu("第8條", 60, 3000, CalcKind.CAPPED_PER_UNIT, actual=200000),
             _line_pu("第8條", 45, 3000, CalcKind.CAPPED_PER_UNIT, actual=200000)]
    assert _flags_of(two, lines, "calc_cap") == []
    r = _result(decision=ClaimDecision.DENY, amount="0").model_copy(
        update={"calculation": [_cap_line("第9條", 18000, 20000), _cap_line("第9條", 4000, 20000)]})
    assert not any(f.check == "calc_cap_sum_exceeded" for f in run_gate(r, _days_bundle(C054, "POL-C-2026-01")).flags)


def test_f2_pol_b_inpatient_surgery_requires_solace_line():
    b = _days_bundle(C054, "POL-B-2026-01")  # 5 日
    assert _flags_of(b, [_lump("第7條", 30000), _line_pu("第12條", 5, 1000)], "calc_structure") == []
    assert _flags_of(b, [_lump("第7條", 30000)], "calc_structure") == ["calc_structure_mismatch"]  # C048 形
    assert _flags_of(b, [_lump("附錄一", 15000)], "calc_structure") == ["calc_structure_mismatch"]
    # 門診手術(第8條)不得有慰問金;次高 0.5 行不算住院手術
    assert _flags_of(b, [_lump("第8條", 15000, "0.5")], "calc_structure") == []
    outpatient_with_solace = _flags_of(b, [_lump("第8條", 15000, "0.5"), _line_pu("第12條", 5, 1000)], "calc_structure")
    assert outpatient_with_solace == ["calc_structure_mismatch"]


@pytest.mark.parametrize("reasoning, flagged", [
    ("要件檢核:手術為牙科處置且並非外傷所致,依第18條不符合給付要求,本案應拒付。", True),  # C046
    ("依第22條等待期已滿;本案核付。", False),
    ("本案無拒付事由,無除外情形,予以核付。", False),  # 否定前綴
    ("不構成第16條除外,不負給付責任之情形不存在。", False),
    ("本公司不負給付保險金之責任,故本案不予給付。", True),
])
def test_f3_decision_reasoning_conflict_keyword(reasoning, flagged):
    r = _result().model_copy(update={"reasoning": reasoning})
    g = run_gate(r, _bundle())
    assert (any(f.check == "decision_reasoning_conflict" for f in g.flags)) is flagged
    assert g.final_decision is (ClaimDecision.MANUAL_REVIEW if flagged else ClaimDecision.APPROVE)


def test_f3_not_applied_to_deny_or_manual():
    r = _result(decision=ClaimDecision.DENY, amount="0").model_copy(update={"reasoning": "本案應拒付。"})
    assert not any(f.check == "decision_reasoning_conflict" for f in run_gate(r, _bundle()).flags)


# --- SPEC 2026-10-02-g:G1 分組接入 P3/F1;G2–G6 提醒型檢核 ---
def test_g1_merged_group_total_must_be_clipped_and_unmerged_excluded():
    b08 = _days_bundle(C008)  # gap 10 → 同組 97 → 只有 90(與各段 51/46)合法
    assert _flags_of(b08, [_line_pu("第7條", 90, 2000), _line_pu("第11條", 90, 1000)], "calc_days") == []
    unclipped = _flags_of(b08, [_line_pu("第7條", 97, 2000), _line_pu("第11條", 97, 1000)], "calc_days")
    assert unclipped == ["calc_days_mismatch"]
    assert _flags_of(b08, [_line_pu("第7條", 51, 2000), _line_pu("第11條", 51, 1000)], "calc_days") == []
    b60 = _days_bundle(C060)  # gap 15 → 兩組:60/45/105 合法,90 不合法(既有 S4)
    assert _flags_of(b60, [_line_pu("第7條", 105, 2000), _line_pu("第11條", 105, 1000)], "calc_days") == []
    merged = _flags_of(b60, [_line_pu("第7條", 90, 2000), _line_pu("第11條", 90, 1000)], "calc_days")
    assert merged == ["calc_days_mismatch"]


def test_g1_cap_sum_uses_stay_groups_not_segments():
    c55 = _days_bundle([("2026-05-01", "2026-05-05", "0"), ("2026-05-15", "2026-05-18", "0")],
                       "POL-C-2026-01")  # gap 10 同組
    split = _flags_of(c55, [_cap_line("第10條", 45000, 60000), _cap_line("第10條", 25000, 60000)], "calc_cap")
    assert split == ["calc_cap_sum_exceeded"]  # 70,000 > 60,000 × 1 組
    c59 = _days_bundle([("2026-07-01", "2026-07-05", "0"), ("2026-07-20", "2026-07-23", "0")],
                       "POL-C-2026-01")  # gap 15 兩組
    assert _flags_of(c59, [_cap_line("第10條", 50000, 60000), _cap_line("第10條", 30000, 60000)], "calc_cap") == []
    # POL-C 第8條逐日限額各段獨立,不受分組影響
    assert _flags_of(c55, [_line_pu("第8條", 5, 3000, CalcKind.CAPPED_PER_UNIT, actual=12000),
                           _line_pu("第8條", 4, 3000, CalcKind.CAPPED_PER_UNIT, actual=10000)], "calc_") == []


def _texted(b: ClaimBundle, *, summary: str | None = None, hospital: str | None = None,
            items: list[tuple[str, int]] | None = None) -> ClaimBundle:
    from app.schemas.claim import ReceiptItem
    dc = b.diagnosis_certificate.model_copy(update={
        **({"treatment_summary": _f(summary)} if summary is not None else {}),
        **({"hospital_name": _f(hospital)} if hospital is not None else {})})
    rc = b.medical_receipt.model_copy(update={
        **({"hospital_name": _f(hospital)} if hospital is not None else {}),
        **({"items": [ReceiptItem(item_name=n, amount=Decimal(a)) for n, a in items]} if items is not None else {})})
    return b.model_copy(update={"diagnosis_certificate": dc, "medical_receipt": rc})


def test_g2_non_nhi_keyword_without_65_percent_ratio_flags():
    b = _texted(_days_bundle(C054, "POL-C-2026-01"), summary="保守治療,自費身分入住非健保特約醫院",
                hospital="<醫院>(非健保特約)")
    full = [_line_pu("第8條", 5, 3000, CalcKind.CAPPED_PER_UNIT, actual=16000), _cap_line("第10條", 25000, 60000)]
    assert "nhi_ratio_review" in _flags_of(b, full, "nhi")
    with_ratio = [ln.model_copy(update={"ratio": Decimal("0.65")}) for ln in full]
    assert _flags_of(b, with_ratio, "nhi") == []
    plain = _texted(_days_bundle(C054, "POL-C-2026-01"), summary="以全民健康保險身分住院", hospital="<醫院>")
    assert _flags_of(plain, full, "nhi") == []
    assert _flags_of(_texted(_days_bundle(C054), summary="自費身分入住非健保特約醫院"),
                     [_line_pu("第7條", 5, 2000), _line_pu("第11條", 5, 1000)], "nhi") == []  # POL-A 無 65% 條款


def test_g3_receipt_room_days_must_match_derived_days():
    b = _texted(_days_bundle([("2026-05-15", "2026-05-16", "0")]),
                items=[("病房費(5日)", 7500), ("藥費", 2400)])  # C009:2 日
    assert "receipt_days_mismatch" in _flags_of(b, [_line_pu("第7條", 2, 2000), _line_pu("第11條", 2, 1000)], "receipt")
    ok = _texted(_days_bundle([("2026-05-15", "2026-05-16", "0")]), items=[("病房費(2日)", 3000)])
    assert _flags_of(ok, [_line_pu("第7條", 2, 2000), _line_pu("第11條", 2, 1000)], "receipt") == []
    two = _texted(_days_bundle(C060), items=[("病房費 60日", 180000), ("病房費(45日)", 135000)])  # 各段日數皆合法
    assert _flags_of(two, [_line_pu("第7條", 105, 2000), _line_pu("第11條", 105, 1000)], "receipt") == []
    nodays = _texted(_days_bundle(C001), items=[("病房費", 7500), ("5日份藥費", 100)])  # 非病房項目不檢
    assert _flags_of(nodays, [_line_pu("第7條", 6, 2000), _line_pu("第11條", 6, 1000)], "receipt") == []


def test_g4_pol_c_receipt_item_outside_known_categories_flags():
    base = _days_bundle(C054, "POL-C-2026-01")
    lines = [_line_pu("第8條", 5, 3000, CalcKind.CAPPED_PER_UNIT, actual=8000), _cap_line("第10條", 9800, 60000)]
    bad = _texted(base, items=[("病房費", 8000), ("藥費", 3600), ("處置費", 4000), ("檢驗費", 2200)])  # C041
    flags = [f for f in run_gate(_result(amount=str(8000 + 9800)).model_copy(update={"calculation": lines}), bad).flags
             if f.check == "receipt_item_unclassified"]
    assert len(flags) == 1 and "處置費" in flags[0].detail
    ok = _texted(base, items=[("病房費", 8000), ("醫療雜費", 3600), ("救護車費", 1800), ("診斷證明書費", 500)])
    assert _flags_of(ok, lines, "receipt_item") == []
    pol_a = _texted(_days_bundle(C054), items=[("處置費", 4000)])  # 僅 POL-C 有第26條分類爭議
    assert _flags_of(pol_a, [_line_pu("第7條", 5, 2000), _line_pu("第11條", 5, 1000)], "receipt_item") == []


@pytest.mark.parametrize("summary, flagged", [
    ("清創及抗生素治療。病患有糖尿病病史五年,長期於本院新陳代謝科門診追蹤", True),  # C005
    ("病患自 2024-08 起於本院婦產科追蹤子宮肌瘤,行子宮肌瘤切除術", True),  # C052
    ("投保前已確診高血壓", True),
    ("靜脈輸液與止瀉治療,症狀緩解後翌日出院", False),
    ("術後門診追蹤兩次,恢復良好", False),  # 「追蹤」單獨出現不算
])
def test_g5_pre_existing_keywords_flag_for_review(summary, flagged):
    b = _texted(_days_bundle(C001), summary=summary)
    flags = _flags_of(b, [_line_pu("第7條", 6, 2000), _line_pu("第11條", 6, 1000)], "pre_")
    assert ("pre_existing_review" in flags) is flagged


def test_g6_unlisted_surgery_name_flags_and_ocr_spaces_ignored():
    base = _days_bundle(C054)
    lines = [_line_pu("第7條", 5, 2000), _line_pu("第11條", 5, 1000), _lump("第10條", 25000)]
    c064 = _flags_of(_texted(base, summary="行甲狀腺結節射頻消融術(經皮微創)"), lines, "unlisted")
    assert "unlisted_surgery_review" in c064
    assert _flags_of(_texted(base, summary="行骨折開放 復位內固定術,合併拔除"), lines, "unlisted") == []  # OCR 空白
    assert _flags_of(_texted(base, summary="行甲狀腺結節射頻消融術"),
                     [_line_pu("第7條", 5, 2000), _line_pu("第11條", 5, 1000)], "unlisted") == []  # 無定額行不檢
    polb = _days_bundle(C054, "POL-B-2026-01")
    listed = _flags_of(_texted(polb, summary="於本診所門診局部麻醉下行脂 肪瘤切除術"), [_lump("第8條", 2500)],
                       "unlisted")
    assert listed == []
    unlisted = _flags_of(_texted(polb, summary="行大腸鏡瘜肉切除"), [_lump("第8條", 2500)], "unlisted")
    assert "unlisted_surgery_review" in unlisted


def test_g1_annual_cap_total_is_a_legal_single_line_shape():
    # C061 真值:三段 70/60/65 不合併,年度累計 195 → 180,模型以單行 180 表達 → 合法;181 不合法
    b = _days_bundle(C061)
    assert _flags_of(b, [_line_pu("第7條", 180, 2000), _line_pu("第11條", 180, 1000)], "calc_days") == []
    over = _flags_of(b, [_line_pu("第7條", 181, 2000), _line_pu("第11條", 181, 1000)], "calc_days")
    assert over == ["calc_days_mismatch"]


def test_g6_ocr_variant_characters_are_normalised():
    base = _days_bundle(C054)
    lines = [_line_pu("第7條", 5, 2000), _line_pu("第11條", 5, 1000), _lump("第10條", 25000)]
    assert _flags_of(_texted(base, summary="行骨折開 放復位内固定術"), lines, "unlisted") == []  # 「内」異體 + 空白


# --- SPEC 2026-10-02-h:引文安全版規則(v6 16 筆不符:A 跳列舉標記 6 / B 表格「第N級手術:項目」6 / C 真改寫 4)---
@pytest.mark.parametrize("pol, clause_no, quote", [
    # R1:跳列舉標記、內容逐字依序(第25條四項、第19條四項、第10條跳到第二項)
    ("POL-A", "第25條", 
     "受益人申領保險金時,應檢具下列文件:保險金申請書、醫師診斷證"
     "明書、醫療費用收據正本或醫院開立之繳費證明、受益人之身分證明。"),
    ("POL-A", "第19條", "但下列妊娠併發症不在此限:子宮外孕、葡萄胎、前置胎盤、子癲前症及子癇症。"),
    ("POL-A", "第10條", "給付「手術保險金」:二、第二級手術:新臺幣貳萬伍仟元。"),
    ("POL-A", "第10條", "一、第一級手術:新臺幣壹萬元。三、第三級手術:新臺幣伍萬元。"),
    ("POL-A", "第2條", 
     "本契約所用名詞定義如下:"
     "二、「住院」:係指被保險人"),  # SPEC-e 列為不符,SPEC-h R1 帶標記跳款改為相符
    ("POL-C", "第10條", 
     "於每次住院新臺幣陸萬元之限額內給付「住院雜費保險金」:醫師指示"
     "用藥及處方藥、醫療器材及衛材費用、檢查、檢驗及放射線診療費用。"),
    ("POL-B", "第22條", 
     "應檢具下列文件:一、保險金申請書;二、醫師診斷證明書(應載明手術名稱及手"
     "術日期);三、醫療費用收據正本或醫院開立之繳費證明;四、受益人之身分證明。"),
    # R2:表格列範圍——每個項目都在該級那一列(含加「手術」、取一項、取多項、尾端省略號)
    ("POL-A", "附錄一", "第二級手術:骨折開放復位內固定術。"),
    ("POL-A", "附錄一", "第二級手術:膽囊切除術、甲狀腺部分切除術..."),
    ("POL-A", "附錄一", "第一級手術:疝氣修補術、...。"),
    ("POL-A", "附錄一", "第二級:膽囊切除術、甲狀腺部分切除術、椎間盤切除術、骨折開放復位內固定術、子宮肌瘤切除術"),
    ("POL-B", "附錄一", "第五級手術:開顱手術"),
    # 誠實保留格界的表格引文(v3 C030 / v4 C065 / v6 C007 / core C016 實例):空白、「-」、整表貼上
    ("POL-B", "附錄一", "第五級 冠狀動脈繞道手術"),
    ("POL-B", "附錄一", "第一級 - 體表腫瘤切除術、痔瘡切除術、包皮環切術、甲溝炎手術、脂肪瘤切除術"),
    ("POL-B", "附錄一", "第一級 體表腫瘤切除術、痔瘡切除術、包皮環切術、甲溝炎手術、脂肪瘤切除術 "
     "第二級 闌尾切除術、疝氣修補術、扁桃腺切除術、白內障晶體置換術、關節鏡半月板修補術"),
    ("POL-A", "附錄一", "等級 | 手術項目(示範列舉) 第一級 | 闌尾切除術、疝氣修補術、痔瘡切除術、"
     "體表腫瘤切除術、扁桃腺切除術"),
])
def test_h_enumeration_markers_and_table_rows_match(pol, clause_no, quote):
    assert _cit_flags(pol, clause_no, quote) == []


@pytest.mark.parametrize("pol, clause_no, quote", [
    # R1 不得矇混:漏項(C050)、錯序、等級/金額重組(含帶標記的)
    ("POL-A", "第25條", "應檢具下列文件:醫師診斷證明書、醫療費用收據正本或醫院開立之繳費證明。"),
    ("POL-A", "第25條", "應檢具下列文件:保險金申請書、受益人之身分證明。"),
    ("POL-A", "第10條", "三、第三級手術:新臺幣伍萬元。一、第一級手術:新臺幣壹萬元。"),
    ("POL-A", "第10條", "二、第二級手術:新臺幣伍萬元。"),
    ("POL-A", "第10條", "第二級手術:新臺幣伍萬元"),
    ("POL-A", "第10條", "給付「手術保險金」:第二級手術:新臺幣貳萬伍仟元。"),  # 無標記的跳項:「:」仍不是分隔符
    # R2 不得矇混:跨列、混入他條金額(C016/C063)、空項目、不存在的等級
    ("POL-A", "附錄一", "第二級手術:冠狀動脈繞道手術"),
    ("POL-A", "附錄一", "第二級手術:膽囊切除術、開顱手術"),
    ("POL-A", "附錄一", "第二級手術: 椎間盤切除術,新臺幣貳萬伍仟元。"),
    ("POL-A", "附錄一", "第二級手術:新臺幣貳萬伍仟元。骨折開放復位內固定術。"),
    ("POL-A", "附錄一", "第四級手術:膽囊切除術"),
    ("POL-B", "附錄一", "第三級手術:開顱手術"),  # POL-B 開顱為第五級
    # C 類真改寫維持不符
    ("POL-A", "第22條", 
     "被保險人自本契約生效日起三十日內因疾病住院診療者,本公"
     "司不負給付責任。因傷害住院診療者,不受前項等待期之限制。"),
    ("POL-B", "第15條", "被保險人因美容整形、外科整形、變性手術"),
])
def test_h_leaked_items_cross_row_and_rewrites_still_mismatch(pol, clause_no, quote):
    assert "citation_quote_mismatch" in _cit_flags(pol, clause_no, quote)


def test_h_empty_items_header_splice_is_flagged():
    # 「第二級手術:」逐字見於第10條故歸條號誤植;只剩省略號的空項目列必須不符(R2 bool(items))
    assert _cit_flags("POL-A", "附錄一", "第二級手術:") == ["citation_clause_misattributed"]
    assert _cit_flags("POL-A", "附錄一", "第二級手術:...") != []  # 逐字見於第10條→條號誤植;R2 不得放行
    # 跨儲存格/跨列拼接:列尾項目黏到下一列等級(讀起來像扁桃腺屬第二級)須由哨兵擋下
    assert "citation_quote_mismatch" in _cit_flags("POL-A", "附錄一", "扁桃腺切除術第二級")
    assert "citation_quote_mismatch" in _cit_flags("POL-A", "附錄一", "等級手術項目")


def test_h_table_row_rule_only_applies_to_table_clauses():
    # 第10條含「第二級手術:」字樣但不是表格:R2 不啟用,跨級金額仍由 R1/原規則擋下
    from app.gate.checks import table_rows
    assert table_rows(load_policy_index()["POL-A"]["第10條"].text) == {}
    rows = table_rows(load_policy_index()["POL-B"]["附錄一"].text)
    assert set(rows) == {"第一級", "第二級", "第三級", "第四級", "第五級"} and "開顱手術" in rows["第五級"]


# --- SPEC 2026-10-02-l:POL-B 多手術案定額行等級對照(route_v1 C051:次高手術未打 5 折;15,000 同為二級全額/三級折半)---
def _polb_multi(summary: str, lines: list[CalcLine], decision: ClaimDecision = ClaimDecision.APPROVE) -> list[str]:
    b = _texted(_days_bundle(C054, "POL-B-2026-01"), summary=summary)
    amount = "0" if decision is not ClaimDecision.APPROVE else "18000"
    r = _result(decision=decision, amount=amount).model_copy(update={"calculation": lines})
    return [f.check for f in run_gate(r, b).flags if f.check == "calc_surgery_grade_mismatch"]


TWO = "同一次全身麻醉下行膽囊切除術併疝氣修補術"  # 第三級 30,000 + 第二級 15,000


def test_l1_second_surgery_full_amount_is_flagged_c051():
    assert _polb_multi(TWO, [_lump("第7條", 30000), _lump("第9條", 15000)]) == ["calc_surgery_grade_mismatch"]


@pytest.mark.parametrize("lines", [
    [_lump("第7條", 30000), _lump("第9條", 15000, "0.5")],  # v8 寫法
    [_lump("第7條", 30000), _lump("第9條", 7500)],  # v5/v7 寫法
    [_lump("附錄一", 30000), _lump("附錄一", 7500)],  # v3 寫法
    [_lump("第7條", 30000), _lump("第9條", 7500), _line_pu("第12條", 5, 1000)],  # 慰問金 per_unit 不受影響
])
def test_l1_correct_half_representations_pass(lines):
    assert _polb_multi(TWO, lines) == []


def test_l2_same_grade_pair_requires_half_on_second():
    same = "同次麻醉行闌尾切除術及疝氣修補術"  # 兩個第二級 15,000
    assert _polb_multi(same, [_lump("第7條", 15000), _lump("第9條", 15000)]) == ["calc_surgery_grade_mismatch"]
    assert _polb_multi(same, [_lump("第7條", 15000), _lump("第9條", 7500)]) == []


def test_l2_not_applied_below_two_names_or_other_policies_or_nonapprove():
    assert _polb_multi("行膽囊切除術", [_lump("第7條", 30000), _lump("第9條", 15000)]) == []  # 只命中一個手術名
    assert _polb_multi("行腹腔鏡手術", [_lump("第7條", 30000), _lump("第9條", 15000)]) == []  # 無命中
    assert _polb_multi(TWO, [_lump("第7條", 30000), _lump("第9條", 15000)], ClaimDecision.DENY) == []
    pola = _texted(_days_bundle(C054), summary=TWO)
    r = _result().model_copy(update={"calculation": [_lump("第10條", 25000), _lump("第10條", 25000)]})
    assert not any(f.check == "calc_surgery_grade_mismatch" for f in run_gate(r, pola).flags)
