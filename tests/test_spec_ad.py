"""SPEC-ad:收據影本 + 他保無法歸桶(unallocated > 0)→ 人工。原僅 prompt 規則 9a 守住,Gate 化(固定解法 27/28)。
SPEC-z 落桶檢核不比對 unallocated,此格若模型仍 approve 會直接放行。"""
from __future__ import annotations

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.schemas.claim import ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload
from tests.test_spec_p import _adj, _capped
from tests.test_spec_z import _adj_c072, _misc, _room

INDEX = load_policy_index()
FLAG = "deduction_unallocated"


def _bundle(is_copy: bool = True, unallocated: int = 5000) -> ClaimBundle:
    p = _bundle_payload("C072")  # 影本;他保病房 12,000
    p["medical_receipt"]["is_copy"] = is_copy
    p["claim_application"]["other_insurer_paid"]["unallocated"] = str(unallocated)
    return ClaimBundle.model_validate(p)


def _hits(adj, b: ClaimBundle) -> list:
    return [f for f in checks.run_gate(adj, b, INDEX).flags if f.check == FLAG]


def test_ad1_copy_with_unallocated_flagged_manual():
    adj = _adj_c072([_room(12000), _misc(70000, 0)])
    g = checks.run_gate(adj, _bundle(), INDEX)
    fl = [f for f in g.flags if f.check == FLAG]
    assert len(fl) == 1 and "5000" in fl[0].detail and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_ad2_copy_without_unallocated_not_flagged():
    assert _hits(_adj_c072([_room(12000), _misc(70000, 0)]), _bundle(unallocated=0)) == []


def test_ad3_original_receipt_with_unallocated_not_flagged():
    assert _hits(_adj_c072([_room(0), _misc(70000, 0)]), _bundle(is_copy=False)) == []


def test_ad4_non_approve_not_checked():
    adj = _adj_c072([_room(12000), _misc(70000, 0)], decision="manual_review")
    assert _hits(adj, _bundle()) == []


def test_ad4_non_pol_c_not_checked():
    p = _bundle_payload("C001")  # POL-A
    p["medical_receipt"]["is_copy"] = True
    p["claim_application"]["other_insurer_paid"]["unallocated"] = "5000"
    line = _capped("第7條", 1000, 2000).model_dump(mode="json")
    adj = _adj("C001", "POL-A", "第7條", calculation=[line])
    assert _hits(adj, ClaimBundle.model_validate(p)) == []
