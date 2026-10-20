"""SPEC-aa:POL-B 第10條同部位十四日內再手術(v13 C047:痔瘡切除術兩次門診各 2,500 → 5,000 放行,真值 2,500)。
摘要只命中一個附錄一手術名、日期相距 ≤ 14 日、手術定額行 ≥ 2 → 改派人工(同疾病/同部位交人工判斷)。"""
from __future__ import annotations

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.schemas.claim import ClaimDecision
from tests.test_spec_p import _adj
from tests.test_spec_w import _bundle, _lump

INDEX = load_policy_index()
FLAG = "surgery_repeat_within_14d"
C047_SUMMARY = ("2026-04-02 門診行痔瘡切除術;"
                "2026-04-12 因同部位術後出血,門診 再行痔瘡切除術止血。兩次皆為門診手術,未辦理住院")


def _run(lines, summary: str = C047_SUMMARY, cid: str = "C047", policy: str = "POL-B", decision: str = "approve"):
    adj = _adj(cid, policy, "第8條", calculation=lines, decision=decision)
    return checks.run_gate(adj, _bundle(cid, summary), INDEX)


def _hits(g) -> list:
    return [f for f in g.flags if f.check == FLAG]


def test_aa1_two_lump_lines_within_14_days_flagged_manual():
    g = _run([_lump("第8條", 2500), _lump("第8條", 2500)])
    assert len(_hits(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_aa1_counts_clause_10_line_as_surgery_line():
    assert _hits(_run([_lump("第8條", 2500), _lump("第10條", 2500)]))


def test_aa2_single_lump_line_not_flagged():
    assert _hits(_run([_lump("第8條", 2500)])) == []


def test_aa3_gap_15_days_not_flagged():
    s = C047_SUMMARY.replace("2026-04-12", "2026-04-17")
    assert _hits(_run([_lump("第8條", 2500), _lump("第8條", 2500)], summary=s)) == []


def test_aa3_gap_exactly_14_days_flagged():
    s = C047_SUMMARY.replace("2026-04-12", "2026-04-16")
    assert _hits(_run([_lump("第8條", 2500), _lump("第8條", 2500)], summary=s))


def test_aa3_single_date_not_flagged():
    s = "2026-04-02 門診行痔瘡切除術,術後再行痔瘡切除術止血"
    assert _hits(_run([_lump("第8條", 2500), _lump("第8條", 2500)], summary=s)) == []


def test_aa4_two_distinct_surgery_names_not_checked():
    names = sorted(checks._appendix_surgery_names("POL-B") - {"痔瘡切除術"})
    s = C047_SUMMARY.replace("再行痔瘡切除術", f"再行{names[0]}")
    assert _hits(_run([_lump("第8條", 2500), _lump("第8條", 2500)], summary=s)) == []


def test_aa5_not_approve_not_checked():
    assert _hits(_run([_lump("第8條", 2500), _lump("第8條", 2500)], decision="manual_review")) == []


def test_aa5_non_pol_b_not_checked():
    g = _run([_lump("第10條", 2500), _lump("第10條", 2500)], cid="C001", policy="POL-A")
    assert _hits(g) == []
