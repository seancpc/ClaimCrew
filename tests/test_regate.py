"""tools/regate.py:離線重跑 Gate(SPEC 2026-10-01-b C3)。"""

from pathlib import Path

from app.schemas.claim import ClaimDecision, GateResult, PipelineOutput
from tests.test_gate import _bundle, _result
from tools.regate import flag_summary, regate


def _write_case(d: Path, cid: str, with_bundle: bool, stale_flags: bool) -> None:
    adj = _result().model_copy(update={"claim_id": cid})
    gate = GateResult(claim_id=cid, original_decision=ClaimDecision.APPROVE,
                      final_decision=ClaimDecision.MANUAL_REVIEW if stale_flags else ClaimDecision.APPROVE,
                      flags=[{"check": "stale", "detail": "舊規則留下的旗標"}] if stale_flags else [],
                      passed=not stale_flags)
    (d / f"{cid}.json").write_text(PipelineOutput(claim_id=cid, adjudication=adj, gate=gate).model_dump_json(),
                                   encoding="utf-8")
    if with_bundle:
        (d / f"{cid}.bundle.json").write_text(_bundle().model_copy(update={"claim_id": cid}).model_dump_json(),
                                              encoding="utf-8")


def test_regate_recomputes_gate_when_bundle_present_and_keeps_original_otherwise(tmp_path: Path):
    _write_case(tmp_path, "C900", with_bundle=True, stale_flags=True)   # 有 bundle:舊旗標應被重算掉
    _write_case(tmp_path, "C901", with_bundle=False, stale_flags=True)  # 無 bundle:原樣保留
    outs = regate(tmp_path)
    assert outs["C900"].gate.passed and outs["C900"].gate.final_decision is ClaimDecision.APPROVE
    assert not outs["C901"].gate.passed and outs["C901"].gate.flags[0].check == "stale"
    summary = flag_summary(outs)
    assert summary == {"cases_with_flag": {"stale": 1}, "flag_instances": {"stale": 1}, "downgraded": 1}


def test_regate_threshold_override_changes_routing(tmp_path: Path, monkeypatch):
    from app.gate import checks
    monkeypatch.setattr(checks, "EXTRACTION_FIELD_CONFIDENCE_THRESHOLD", checks.EXTRACTION_FIELD_CONFIDENCE_THRESHOLD)
    _write_case(tmp_path, "C902", with_bundle=True, stale_flags=False)  # 測試 bundle 欄位信心 0.95
    assert regate(tmp_path, field_threshold=0.99)["C902"].gate.final_decision is ClaimDecision.MANUAL_REVIEW
    assert regate(tmp_path, field_threshold=0.80)["C902"].gate.final_decision is ClaimDecision.APPROVE
