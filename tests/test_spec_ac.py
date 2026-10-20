"""SPEC-ac:POL-B 單一手術等級值(v14 C029:膽囊切除術第三級 30,000 填 60,000,合法等級值被放行)。
摘要恰命中 1 個附錄一手術名 → 第7條/附錄一定額行 = 等級定額 G、第8條門診定額行 = G × 50%;否則改派人工。"""
from __future__ import annotations

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.schemas.claim import ClaimDecision
from tests.test_spec_p import _adj
from tests.test_spec_w import _bundle, _lump

INDEX = load_policy_index()
FLAG = "calc_surgery_grade_value"
C029_SUMMARY = "行膽囊切除術,住院5日"


def _run(lines, summary: str = C029_SUMMARY, cid: str = "C029", policy: str = "POL-B", decision: str = "approve"):
    adj = _adj(cid, policy, "第7條", calculation=lines, decision=decision)
    return checks.run_gate(adj, _bundle(cid, summary), INDEX)


def _hits(g) -> list:
    return [f for f in g.flags if f.check == FLAG]


def test_ac1_wrong_grade_value_flagged_manual():
    g = _run([_lump("第7條", 60000)])
    assert len(_hits(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_ac1_appendix_clause_line_also_checked():
    assert _hits(_run([_lump("附錄一", 60000)]))


def test_ac2_correct_inpatient_value_not_flagged():
    assert _hits(_run([_lump("第7條", 30000)])) == []


def test_ac2_correct_outpatient_half_not_flagged():
    assert _hits(_run([_lump("第8條", 15000)], summary="門診行膽囊切除術")) == []


def test_ac3_outpatient_full_amount_flagged():
    assert _hits(_run([_lump("第8條", 30000)], summary="門診行膽囊切除術"))


def test_ac3_inpatient_half_amount_flagged():
    assert _hits(_run([_lump("第7條", 15000)]))


def test_ac4_two_surgery_names_not_checked_here():
    other = sorted(checks._appendix_surgery_names("POL-B") - {"膽囊切除術"})[0]
    g = _run([_lump("第7條", 60000)], summary=f"行膽囊切除術及{other},住院5日")
    assert [f for f in _hits(g) if "POL-B" in f.detail] == []


def test_ac5_no_appendix_name_not_checked():
    assert _hits(_run([_lump("第7條", 60000)], summary="行某項手術,住院5日")) == []


def test_ac5_not_approve_not_checked():
    assert _hits(_run([_lump("第7條", 60000)], decision="manual_review")) == []


def test_ac5_non_surgery_clause_line_ignored():
    assert _hits(_run([_lump("第7條", 30000), _lump("第12條", 5000)])) == []


def test_ac4_two_names_branch_does_not_apply_single_value():
    """兩個手術名(第三級膽囊 + 第五級肺葉)填膽囊全額 30,000:單一手術分支不得以最高等級 G 判不符。"""
    g = _run([_lump("第7條", 30000)], summary="行膽囊切除術及肺葉切除術,住院5日")
    assert [f for f in _hits(g) if "POL-B" in f.detail] == []
