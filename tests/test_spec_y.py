"""SPEC-y:C073 日額型他保不扣(POL-C 第14條觸發要件為「收據正本已用於他保」)。
Y1 真值 / Y3 Gate `deduction_without_copy` / Y4 prompt 規則 9a 以 is_copy 為唯一觸發 / Y5 regate 缺件仍評分。"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.schemas.claim import ClaimBundle, ClaimDecision
from eval.run_eval import load_truths, selfcheck_predictions
from tests.test_pipeline import _bundle_payload
from tests.test_spec_p import _adj, _capped, _capped_per_unit
from tools import regate as regate_tool

ROOT = Path(__file__).resolve().parents[1]
INDEX = load_policy_index()
FLAG = "deduction_without_copy"


def _claim(cid: str) -> dict:
    return json.loads((ROOT / "data" / "claims" / cid / "claim.json").read_text(encoding="utf-8"))


# ---------- Y1 真值 ----------
def test_y1_c073_truth_is_full_approve_without_deduction():
    t = load_truths()["C073"]
    assert t["decision"] == "approve" and t["approved_amount"] == "78000"
    assert t["clause_basis"] == ["POL-C 第8條", "POL-C 第10條", "POL-C 第14條"]


def test_y1_c073_differs_from_c072_only_in_copy_flag_and_other_insurer_bucket():
    a, b = _claim("C072")["documents"], _claim("C073")["documents"]
    assert b["medical_receipt"]["is_copy"] is False and a["medical_receipt"]["is_copy"] is True
    assert b["claim_application"]["other_insurer_paid"] == {
        "room_fee": "0", "surgery_fee": "0", "misc_fee": "0", "ambulance_fee": "0", "unallocated": "14000"}
    for doc in ("diagnosis_certificate", "incident_statement"):
        assert a[doc] == b[doc]
    ra, rb = dict(a["medical_receipt"]), dict(b["medical_receipt"])
    ra.pop("is_copy"), rb.pop("is_copy")
    assert ra == rb
    ca, cb = dict(a["claim_application"]), dict(b["claim_application"])
    ca.pop("other_insurer_paid"), cb.pop("other_insurer_paid")
    assert ca == cb


def test_y1_c073_pdfs_rendered():
    assert sorted(p.name for p in (ROOT / "data" / "claims" / "C073" / "pdf").iterdir()) == [
        "claim_application.pdf", "diagnosis_certificate.pdf", "medical_receipt.pdf"]


# ---------- Y3 Gate ----------
def _bundle(is_copy: bool) -> ClaimBundle:
    p = _bundle_payload("C072")
    p["medical_receipt"]["is_copy"] = is_copy
    return ClaimBundle.model_validate(p)


def _adj_ded(room_deduction: int, decision: str = "approve"):
    calc = [_capped_per_unit("第8條", 7, 3000, 18000) | {"deduction": str(room_deduction)},
            _capped("第10條", 70000, 60000).model_dump(mode="json")]
    return _adj("C072", "POL-C", "第8條", calculation=calc, decision=decision)


def _flags(adj, b: ClaimBundle) -> list:
    return [f for f in checks.run_gate(adj, b, INDEX).flags if f.check == FLAG]


def test_y3_original_receipt_with_deduction_is_flagged_and_routed_manual():
    g = checks.run_gate(_adj_ded(14000), _bundle(False), INDEX)
    fl = [f for f in g.flags if f.check == FLAG]
    assert len(fl) == 1 and "14000" in fl[0].detail and "第14條" in fl[0].detail
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_y3_copy_receipt_with_deduction_c072_shape_not_flagged():
    assert _flags(_adj_ded(12000), _bundle(True)) == []


def test_y3_original_receipt_without_deduction_c073_answer_not_flagged():
    assert _flags(_adj_ded(0), _bundle(False)) == []


def test_y3_non_approve_not_flagged():
    assert _flags(_adj_ded(14000, decision="manual_review"), _bundle(False)) == []


def test_y3_other_policies_not_checked():
    p = _bundle_payload("C001")  # POL-A
    p["medical_receipt"]["is_copy"] = False
    b = ClaimBundle.model_validate(p)
    line = _capped("第7條", 1000, 2000, deduction=500).model_dump(mode="json")
    adj = _adj("C001", "POL-A", "第7條", calculation=[line])
    assert _flags(adj, b) == []


# ---------- Y4 prompt ----------
def _rule_9a() -> str:
    text = (ROOT / "app" / "agents" / "prompts" / "adjudication.md").read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("9a."))


def test_y4_rule_9a_triggers_deduction_only_on_copy():
    """SPEC-y2 重試(SPEC-z 落桶檢核上線後,固定解法 27)。"""
    r = _rule_9a()
    assert "僅在 `medical_receipt.is_copy` 為 true 時" in r and "任一桶非零時" not in r


def test_y4_rule_9a_names_bucket_to_clause_mapping():
    r = _rule_9a()
    for bucket, clause in (("room_fee", "第8條"), ("surgery_fee", "第9條"), ("misc_fee", "第10條"),
                           ("ambulance_fee", "第11條")):
        assert f"`{bucket}` → {clause}" in r
    assert "只能填在對應條款的明細行" in r


def test_y4_rule_9a_original_receipt_never_deducts_nor_routes_manual():
    r = _rule_9a()
    assert "`is_copy` 為 false" in r and "一律不扣除" in r and "不因他保欄位轉人工" in r


# ---------- Y5 regate ----------
def test_y5_regate_scores_available_cases_and_lists_missing(tmp_path: Path):
    preds = selfcheck_predictions(load_truths(), INDEX)
    for cid, o in preds.items():
        if cid != "C073":
            (tmp_path / f"{cid}.json").write_text(o.model_dump_json(), encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        regate_tool.main([str(tmp_path)])
    report = json.loads(buf.getvalue())
    assert report["eval"]["missing"] == ["C073"] and report["eval"]["n_scored"] == 72
    assert report["eval"]["accuracy"] == 1.0 and Decimal(str(report["eval"]["n_cases"])) == 73
