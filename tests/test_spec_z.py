"""SPEC-z:他保扣除落桶檢核(v12 C072:病房費他保 12,000 扣在第10條雜費桶,58,000 < 60,000 仍在限額內 → 放行 +10,000)。
收據影本時,各條扣除額合計須 = 申請書對應桶他保金額(第8條↔病房、第9條↔手術、第10條↔雜費、第11條↔救護車)。"""
from __future__ import annotations

from typing import Any

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.schemas.claim import ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload
from tests.test_spec_p import _adj, _capped, _capped_per_unit

INDEX = load_policy_index()
FLAG = "deduction_bucket_mismatch"


def _bundle(is_copy: bool = True) -> ClaimBundle:
    p = _bundle_payload("C072")  # 影本;他保病房 12,000、其餘 0
    p["medical_receipt"]["is_copy"] = is_copy
    return ClaimBundle.model_validate(p)


def _room(ded: int) -> dict[str, Any]:
    return _capped_per_unit("第8條", 7, 3000, 18000) | {"deduction": str(ded)}


def _misc(actual: int, ded: int) -> dict[str, Any]:
    return _capped("第10條", actual, 60000, deduction=ded).model_dump(mode="json")


def _adj_c072(lines: list[dict[str, Any]], decision: str = "approve"):
    return _adj("C072", "POL-C", "第8條", calculation=lines, decision=decision)


def _flags(adj, b: ClaimBundle) -> list:
    return [f for f in checks.run_gate(adj, b, INDEX).flags if f.check == FLAG]


def test_z1_deduction_in_matching_bucket_not_flagged():
    assert _flags(_adj_c072([_room(12000), _misc(70000, 0)]), _bundle()) == []


def test_z2_deduction_in_wrong_bucket_flagged_and_routed_manual():
    adj = _adj_c072([_room(0), _misc(70000, 12000)])  # v12 C072 形狀:76,000 放行
    g = checks.run_gate(adj, _bundle(), INDEX)
    fl = [f for f in g.flags if f.check == FLAG]
    assert len(fl) == 1 and "第8條" in fl[0].detail and "第10條" in fl[0].detail and "12000" in fl[0].detail
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_z3_copy_receipt_without_any_deduction_flagged():
    assert len(_flags(_adj_c072([_room(0), _misc(70000, 0)]), _bundle())) == 1


def test_z4_split_lines_same_clause_sum_matches():
    lines = [_room(12000), _misc(42000, 0), _misc(28000, 0)]
    assert _flags(_adj_c072(lines), _bundle()) == []


def test_z5_original_receipt_non_approve_and_other_policy_not_checked():
    assert _flags(_adj_c072([_room(0), _misc(70000, 12000)]), _bundle(is_copy=False)) == []
    assert _flags(_adj_c072([_room(0), _misc(70000, 12000)], decision="manual_review"), _bundle()) == []
    p = _bundle_payload("C001")  # POL-A
    p["medical_receipt"]["is_copy"] = True
    line = _capped("第7條", 1000, 2000, deduction=500).model_dump(mode="json")
    adj = _adj("C001", "POL-A", "第7條", calculation=[line])
    assert _flags(adj, ClaimBundle.model_validate(p)) == []


def test_z6_deduction_split_across_lines_of_same_clause_is_summed():
    a = _capped_per_unit("第8條", 4, 3000, 10000) | {"deduction": "7000"}
    b = _capped_per_unit("第8條", 3, 3000, 8000) | {"deduction": "5000"}
    assert _flags(_adj_c072([a, b, _misc(70000, 0)]), _bundle()) == []
