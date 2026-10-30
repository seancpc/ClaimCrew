"""SPEC-w:「合法值但非本案值」三道檢核(2026-10-05,11 輪歷史重放 114 件金額錯中 3 件未被金額類檢核接住)。
W1 POL-A 手術定額須 = 摘要最高等級定額(v2 C054 填第三級 50,000 被放行,真值 25,000)
W2 POL-C 第9條限額須 = 摘要最高等級限額(v8 C035 第三級填 40,000)
W3 多段住院日數型條款合計須涵蓋全部住院段(v8 C060 兩段 60+45 只填 60)"""
from __future__ import annotations

import re
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.gate.reference import POLC_SURGERY_CAP
from app.schemas.claim import ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload, _calc_line
from tests.test_spec_p import _adj, _capped, _capped_per_unit

INDEX = load_policy_index()
GRADE = "calc_surgery_grade_value"
DAYS = "calc_stay_days_uncovered"


def _flags(adj, bundle: ClaimBundle, kind: str) -> list:
    return [f for f in checks.run_gate(adj, bundle, INDEX).flags if f.check == kind]


def _bundle(cid: str, summary: str | None = None) -> ClaimBundle:
    p = _bundle_payload(cid)
    if summary is not None:
        p["diagnosis_certificate"]["treatment_summary"]["value"] = summary
    return ClaimBundle.model_validate(p)


def _lump(clause: str, amount: int) -> dict[str, Any]:
    d = _calc_line(clause, 0, "0")
    d.update(kind="lump_sum", lump_amount=str(amount))
    return d


def _pol_a(cid: str, lumps: list[int], days: int = 5, decision: str = "approve"):
    calc = [_calc_line("第7條", days, "2000"), _calc_line("第11條", days, "1000"),
            *[_lump("第10條", x) for x in lumps]]
    return _adj(cid, "POL-A", "第7條", calculation=calc, decision=decision)


# ---------- W1 POL-A 手術等級 ----------
def test_w1_1_single_grade2_surgery_rejects_grade3_lump():
    b = _bundle("C054", "行膽囊切除術")
    fl = _flags(_pol_a("C054", [50000]), b, GRADE)
    assert len(fl) == 1 and "25000" in fl[0].detail and "50000" in fl[0].detail
    assert checks.run_gate(_pol_a("C054", [50000]), b, INDEX).final_decision is ClaimDecision.MANUAL_REVIEW


def test_w1_2_single_grade2_surgery_accepts_grade2_lump():
    assert _flags(_pol_a("C054", [25000]), _bundle("C054", "行膽囊切除術"), GRADE) == []


def test_w1_3_two_surgeries_pay_highest_only_no_second_line():
    b = _bundle("C054")  # 膽囊(二級)+ 疝氣(一級);POL-A 第10條次高不給
    assert len(_flags(_pol_a("C054", [25000, 10000]), b, GRADE)) == 1
    assert _flags(_pol_a("C054", [25000]), b, GRADE) == []


def test_w1_4_c054_v2_shape_grade3_lump_with_two_lower_surgeries_is_flagged():
    assert len(_flags(_pol_a("C054", [50000]), _bundle("C054"), GRADE)) == 1


def test_w1_5_no_listed_surgery_in_summary_does_not_flag():
    assert _flags(_pol_a("C054", [50000]), _bundle("C054", "<未列於附錄一之處置>"), GRADE) == []


def test_w1_6_multi_stay_bundle_is_not_checked():
    b = _bundle("C060", "行膽囊切除術")  # 兩段住院:手術等級屬於哪一段無法自摘要判定 → 已知限制,不檢
    adj = _adj("C060", "POL-A", "第7條", calculation=[_calc_line("第7條", 105, "2000"),
                                                      _calc_line("第11條", 105, "1000"), _lump("第10條", 50000)])
    assert _flags(adj, b, GRADE) == []


# ---------- W2 POL-C 第9條限額 ----------
def _pol_c(cap: int, decision: str = "approve"):
    calc = [_capped_per_unit("第8條", 11, 3000, 33000), _capped("第9條", 95000, cap).model_dump(mode="json"),
            _capped("第10條", 52000, 60000).model_dump(mode="json")]
    return _adj("C035", "POL-C", "第8條", calculation=calc, decision=decision)


def test_w2_1_grade3_surgery_rejects_grade2_cap():
    fl = _flags(_pol_c(40000), _bundle("C035"), GRADE)
    assert len(fl) == 1 and "80000" in fl[0].detail and "40000" in fl[0].detail


def test_w2_2_grade3_surgery_accepts_full_cap():
    assert _flags(_pol_c(80000), _bundle("C035"), GRADE) == []


def test_w2_3_grade1_surgery_requires_quarter_cap():
    b = _bundle("C035", "行闌尾切除術")
    assert _flags(_pol_c(20000), b, GRADE) == []
    assert len(_flags(_pol_c(80000), b, GRADE)) == 1


def test_w2_4_no_listed_surgery_does_not_flag():
    assert _flags(_pol_c(40000), _bundle("C035", "<未列於附錄一之處置>"), GRADE) == []


def test_w2_5_cap_constant_matches_clause_9_text():
    text = INDEX["POL-C"]["第9條"].text
    base = {"捌萬": 80000}[re.search(r"新臺幣(捌萬)元之百分之", text).group(1)]
    pct = {"二十五": 25, "五十": 50, "一百": 100}
    found = [pct[m] for m in re.findall(r"百分之(二十五|五十|一百)", text)]
    assert {g: base * p // 100 for g, p in zip((1, 2, 3), found, strict=True)} == POLC_SURGERY_CAP


# ---------- W3 多段住院日數涵蓋 ----------
def _c060(qtys: list[int], decision: str = "approve"):
    calc = [_calc_line("第7條", 105, "2000"), *[_calc_line("第11條", q, "1000") for q in qtys]]
    return _adj("C060", "POL-A", "第7條", calculation=calc, decision=decision)


def test_w3_1_c060_shape_only_first_stay_is_flagged():
    fl = _flags(_c060([60]), _bundle("C060"), DAYS)
    assert len(fl) == 1 and "60" in fl[0].detail and "105" in fl[0].detail
    assert checks.run_gate(_c060([60]), _bundle("C060"), INDEX).final_decision is ClaimDecision.MANUAL_REVIEW


def test_w3_2_full_total_single_line_or_split_lines_pass():
    b = _bundle("C060")
    assert _flags(_c060([105]), b, DAYS) == []
    assert _flags(_c060([60, 45]), b, DAYS) == []


def test_w3_3_single_admission_never_flags():
    b = _bundle("C054")  # 單段 5 日
    assert _flags(_pol_a("C054", [25000], days=3), b, DAYS) == []


def test_w3_4_pol_a_annual_cap_accepts_180_for_195_total():
    b = _bundle("C061")  # 三段 70+60+65 = 195 → 年度上限 180
    calc = [_calc_line("第7條", 180, "2000"), _calc_line("第11條", 180, "1000")]
    adj = _adj("C061", "POL-A", "第7條", calculation=calc)
    assert _flags(adj, b, DAYS) == []


def test_w3_5_pol_c_merged_stays_use_group_total():
    b = _bundle("C055")  # 5+4 日間隔 ≤14 合併為 9
    one = _adj("C055", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 5, 3000, 12000)])
    full = _adj("C055", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 5, 3000, 12000),
                                                       _capped_per_unit("第8條", 4, 3000, 10000)])
    assert len(_flags(one, b, DAYS)) == 1 and _flags(full, b, DAYS) == []


@given(st.lists(st.integers(min_value=1, max_value=104), min_size=0, max_size=4))
def test_w3_property_split_lines_flag_iff_sum_differs_from_total(cuts):
    pts = sorted(set(cuts))
    parts = [b - a for a, b in zip([0, *pts], [*pts, 105], strict=True)]  # 任意拆成合計 105 的多行
    b = _bundle("C060")
    assert _flags(_c060(parts), b, DAYS) == []
    assert len(_flags(_c060(parts[:-1] or [1]), b, DAYS)) == 1  # 少一段 → 合計 ≠ 105


# ---------- W4 非核准案 ----------
def test_w4_non_approve_never_flags():
    assert _flags(_pol_a("C054", [50000], decision="deny"), _bundle("C054"), GRADE) == []
    assert _flags(_pol_c(40000, decision="deny"), _bundle("C035"), GRADE) == []
    assert _flags(_c060([60], decision="deny"), _bundle("C060"), DAYS) == []


def test_w1_7_reuses_no_existing_flag_name_so_l1_pol_b_scope_is_unchanged():
    """SPEC-w 修訂:W1/W2 用新旗標名,既有 L1 `calc_surgery_grade_mismatch` 仍只屬 POL-B(test_gate L2 範圍斷言不動)。"""
    g = checks.run_gate(_pol_a("C054", [25000, 25000]), _bundle("C054"), INDEX)
    kinds = {f.check for f in g.flags}
    assert GRADE in kinds and "calc_surgery_grade_mismatch" not in kinds
