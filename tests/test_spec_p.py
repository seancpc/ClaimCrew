"""SPEC-p:方案 P——P1 明細合併、P2 deny 類旗標改派 deny、P3 分段逐日限額檢核、P4 regate 重 finalize、
P5 Repair 顧問模式。"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.gate import calc, checks
from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, CalcLine, ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload, _calc_line, _decision_payload

INDEX = load_policy_index()
_COMPUTED = {"approved_amount", "undisputed_amount"}  # 程式算出、不屬草稿的欄位(SPEC-x 加入可確定金額)


def _capped(clause: str, actual: int, cap: int, *, deduction: int = 0, ratio: str = "1",
            item: str = "<項目>") -> CalcLine:
    d = _calc_line(clause)
    d.update(item=item, kind="capped_actual", quantity=0, unit_amount="0", actual_amount=str(actual), cap=str(cap),
             deduction=str(deduction), ratio=ratio)
    return CalcLine.model_validate(d)


def _per_unit(clause: str, qty: int, unit: int) -> CalcLine:
    return CalcLine.model_validate(_calc_line(clause, qty, str(unit)))


# ---------- P1 合併 ----------
def test_p1_merges_same_clause_capped_actual_lines_and_caps_the_sum():
    lines = [_capped("第9條", 18000, 20000, item="手術費"), _capped("第9條", 4000, 20000, item="麻醉費")]
    out = calc.normalize_calculation(lines)
    assert len(out) == 1 and out[0].actual_amount == Decimal(22000) and out[0].cap == Decimal(20000)
    assert out[0].item == "手術費+麻醉費" and calc.total_amount(out) == Decimal(20000)
    assert calc.total_amount(lines) == Decimal(22000)  # 合併前各自套限額的錯誤值


def test_p1_does_not_merge_different_cap_ratio_kind_or_clause_and_keeps_order():
    a = _capped("第9條", 1000, 20000)
    b = _capped("第9條", 1000, 40000)              # cap 不同
    c = _capped("第9條", 1000, 20000, ratio="0.65")  # ratio 不同
    d = _per_unit("第9條", 1, 1000)                 # kind 不同
    e = _capped("第10條", 1000, 20000)             # 條號不同
    f = _capped("第9條", 500, 20000, deduction=100)  # 與 a 同鍵 → 併入 a
    out = calc.normalize_calculation([a, b, c, d, e, f])
    assert [ln.clause_no for ln in out] == ["第9條", "第9條", "第9條", "第9條", "第10條"]
    assert out[0].actual_amount == Decimal(1500) and out[0].deduction == Decimal(100)
    assert out[1] == b and out[2] == c and out[3] == d and out[4] == e


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["第9條", "第10條"]), st.integers(0, 50000), st.sampled_from([20000, 60000])),
                min_size=0, max_size=6))
def test_p1_property_idempotent_and_never_increases_total(spec):
    lines = [_capped(cl, act, cap) for cl, act, cap in spec]
    once = calc.normalize_calculation(lines)
    assert calc.normalize_calculation(once) == once
    assert calc.total_amount(once) <= calc.total_amount(lines)
    assert sum(ln.actual_amount for ln in once) == sum(ln.actual_amount for ln in lines)  # 事實總和不變


def test_p1_finalize_applies_normalization():
    d = _decision_payload("C003", "POL-C", "第9條")["adjudication"]
    d["calculation"] = [json.loads(_capped("第9條", 18000, 20000).model_dump_json()),
                        json.loads(_capped("第9條", 4000, 20000).model_dump_json())]
    draft = AdjudicationDraft.model_validate(d)
    default = finalize_adjudication(draft)  # SPEC-q 修訂:收據覆蓋檢核補洞後預設合併;False 為量測用 opt-out
    assert len(default.calculation) == 1 and default.approved_amount == Decimal(20000)
    off = finalize_adjudication(draft, merge_split_lines=False)
    assert len(off.calculation) == 2 and off.approved_amount == Decimal(22000)


# ---------- P2 deny 類旗標 ----------
def _adj(cid: str, policy: str, clause: str, **over: Any):
    d = _decision_payload(cid, policy, clause)["adjudication"]
    d.update(over)
    return finalize_adjudication(AdjudicationDraft.model_validate(d))


def test_p2_waiting_period_flag_routes_to_deny_not_manual():
    bundle = ClaimBundle.model_validate(_bundle_payload("C004"))  # 合成案:生效日距事件日 18 日
    adj = _adj("C004", "POL-A", "第7條")
    g = checks.run_gate(adj, bundle, INDEX)
    assert any(f.check == "waiting_period_unmet" for f in g.flags)
    assert g.final_decision is ClaimDecision.DENY and g.passed is False and g.original_decision is ClaimDecision.APPROVE


def test_p2_deny_dominates_other_routing_flags_and_deadline_also_denies():
    bundle = ClaimBundle.model_validate(_bundle_payload("C004"))
    bad_cit = {"policy_id": "POL-A", "clause_no": "第7條", "quote": "不存在的引文", "relevance": "x"}
    adj = _adj("C004", "POL-A", "第7條", citations=[bad_cit])
    g = checks.run_gate(adj, bundle, INDEX)
    assert {"waiting_period_unmet", "citation_quote_mismatch"} <= {f.check for f in g.flags}
    assert g.final_decision is ClaimDecision.DENY
    b67 = ClaimBundle.model_validate(_bundle_payload("C067"))  # 合成案:申請逾二年時效
    g67 = checks.run_gate(_adj("C067", "POL-A", "第7條"), b67, INDEX)
    assert any(f.check == "claim_deadline_exceeded" for f in g67.flags) and g67.final_decision is ClaimDecision.DENY


def test_p2_not_applied_when_effective_date_low_confidence(monkeypatch):
    payload = _bundle_payload("C004")
    payload["claim_application"]["policy_effective_date"]["confidence"] = 0.3  # pre-mortem:生效日抽取不可信
    bundle = ClaimBundle.model_validate(payload)
    g = checks.run_gate(_adj("C004", "POL-A", "第7條"), bundle, INDEX)
    assert any(f.check == "waiting_period_unmet" for f in g.flags)
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW  # 低信心 → 維持人工,不自動拒付


def test_p2_original_deny_or_manual_untouched():
    bundle = ClaimBundle.model_validate(_bundle_payload("C004"))
    g = checks.run_gate(_adj("C004", "POL-A", "第7條", decision="manual_review", calculation=[]), bundle, INDEX)
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW
    assert not any(f.check == "waiting_period_unmet" for f in g.flags)


# ---------- P3 分段逐日限額 ----------
def _capped_per_unit(clause: str, qty: int, unit: int, actual: int) -> dict[str, Any]:
    d = _calc_line(clause, qty, str(unit))
    d.update(kind="capped_per_unit", actual_amount=str(actual))
    return d


def test_p3_segment_cap_flags_c055_shape_and_accepts_per_segment_lines():
    bundle = ClaimBundle.model_validate(_bundle_payload("C055"))  # 兩段 5+4 日;病房費 12,000 / 10,000
    one_line = _adj("C055", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 9, 3000, 27000)])
    g = checks.run_gate(one_line, bundle, INDEX)
    seg = [f for f in g.flags if f.check == "calc_segment_cap_mismatch"]
    assert len(seg) == 1 and "22000" in seg[0].detail and "27000" in seg[0].detail
    two_lines = _adj("C055", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 5, 3000, 12000),
                                                            _capped_per_unit("第8條", 4, 3000, 10000)])
    assert not any(f.check == "calc_segment_cap_mismatch" for f in checks.run_gate(two_lines, bundle, INDEX).flags)


def test_p3_not_applied_for_single_segment_or_unmatched_room_items():
    bundle = ClaimBundle.model_validate(_bundle_payload("C003"))  # 單段住院
    single = _adj("C003", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 4, 3000, 7500)])
    g = checks.run_gate(single, bundle, INDEX)
    assert not any(f.check == "calc_segment_cap_mismatch" for f in g.flags)
    payload = _bundle_payload("C055")
    payload["medical_receipt"]["items"] = [i for i in payload["medical_receipt"]["items"]
                                          if "第2次" not in i["item_name"] or "病房" not in i["item_name"]]
    bundle2 = ClaimBundle.model_validate(payload)  # 病房項目 1 個 ≠ 段數 2 → 不檢
    merged = _adj("C055", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 9, 3000, 27000)])
    g2 = checks.run_gate(merged, bundle2, INDEX)
    assert not any(f.check == "calc_segment_cap_mismatch" for f in g2.flags)


# ---------- P4 regate 重新 finalize ----------
def test_p4_regate_refinalizes_split_lines_before_gate(tmp_path):
    from app.schemas.claim import AdjudicationResult, GateResult, PipelineOutput
    from tools import regate as rg
    d = _decision_payload("C003", "POL-C", "第9條")["adjudication"]
    d["calculation"] = [json.loads(_capped("第9條", 18000, 20000).model_dump_json()),
                        json.loads(_capped("第9條", 4000, 20000).model_dump_json())]
    stored = AdjudicationResult(**d, approved_amount=Decimal(22000))  # 存檔時各自套限額的舊值
    gate = GateResult(claim_id="C003", original_decision=ClaimDecision.APPROVE, final_decision=ClaimDecision.APPROVE,
                      flags=[], passed=True)
    po = PipelineOutput(claim_id="C003", adjudication=stored, gate=gate)
    (tmp_path / "C003.json").write_text(po.model_dump_json(), encoding="utf-8")
    (tmp_path / "C003.bundle.json").write_text(json.dumps(_bundle_payload("C003"), ensure_ascii=False),
                                               encoding="utf-8")
    kept = rg.regate(tmp_path, merge_split_lines=False)["C003"]  # opt-out:不合併,cap-sum 旗標照舊接住
    assert len(kept.adjudication.calculation) == 2 and any(f.check == "calc_cap_sum_exceeded" for f in kept.gate.flags)
    out = rg.regate(tmp_path)["C003"]  # SPEC-q 後預設合併
    assert len(out.adjudication.calculation) == 1 and out.adjudication.approved_amount == Decimal(20000)
    assert not any(f.check == "calc_cap_sum_exceeded" for f in out.gate.flags)


# ---------- P5 Repair 顧問模式 ----------
def test_p5_advisory_keeps_adjudication_and_gate_but_records_suggestion():
    from app.pipeline import repair as rp
    from tests.test_repair import CALC11, REF, FakeAgent, _decision_json, _final, _good_adj, _output
    bundle = ClaimBundle.model_validate(_bundle_payload("C001"))
    orig = _good_adj("C001")
    orig = orig.model_copy(update={"calculation": [orig.calculation[0]], "citations": [orig.citations[0]]})
    orig = finalize_adjudication(AdjudicationDraft.model_validate(orig.model_dump(exclude=_COMPUTED)))
    out = _output(orig, [CALC11])
    repaired = _good_adj("C001")
    agent = FakeAgent([_final("r1", _decision_json(repaired))])
    res = rp.repair_case(out, bundle, INDEX, agent, REF, second_opinion_fn=lambda: orig)  # 預設 advisory=True
    assert res.adjudication == out.adjudication and res.gate == out.gate  # 不放行、不改派
    assert res.repair is not None and res.repair.invoked and res.repair.applied is False
    assert res.repair.would_release is True and res.repair.guard_reason == "released"
    assert res.repair.repaired_decision is ClaimDecision.APPROVE and res.repair.repaired_amount == Decimal(18000)
    res2 = rp.repair_case(out, bundle, INDEX, FakeAgent([_final("r1", _decision_json(repaired))]), REF,
                          second_opinion_fn=lambda: orig, advisory=False)
    assert res2.repair is not None and res2.repair.applied is True and res2.adjudication == repaired  # 量測用放行路徑


def test_p5_rerepair_release_flag_and_run_pipeline_advisory(tmp_path, monkeypatch):
    from app.schemas.claim import PipelineOutput
    from tests.test_agent_probe import FakeProject, FakeResponses
    from tests.test_repair import CALC11, _decision_json, _final, _good_adj, _output
    from tools import rerepair as rr
    pred = tmp_path / "pred"
    pred.mkdir()
    orig = _good_adj("C001")
    orig = finalize_adjudication(AdjudicationDraft.model_validate({**orig.model_dump(exclude=_COMPUTED),
                                                                   "calculation": [orig.calculation[0].model_dump()],
                                                                   "citations": [orig.citations[0].model_dump()]}))
    (pred / "C001.json").write_text(_output(orig, [CALC11]).model_dump_json(), encoding="utf-8")
    (pred / "C001.bundle.json").write_text(json.dumps(_bundle_payload("C001"), ensure_ascii=False), encoding="utf-8")
    repaired = _good_adj("C001")
    for release in (False, True):
        project = FakeProject(FakeResponses([_final("r1", _decision_json(repaired))]))
        monkeypatch.setattr(rr, "make_project_client", lambda endpoint, p=project: p)
        monkeypatch.setattr(rr, "AOAIClient", lambda: None)
        out_dir = tmp_path / ("rel" if release else "adv")
        args = [str(pred), "--out", str(out_dir), "--endpoint", "https://x/api/projects/p"]
        if release:
            args.append("--release")
        assert rr.main(args) == 0
        o = PipelineOutput.model_validate_json((out_dir / "C001.json").read_text(encoding="utf-8"))
        assert o.repair is not None and o.repair.applied is release
        assert (o.adjudication == repaired) is release and (o.gate.passed is True) is release


def test_p1_no_merge_when_two_stay_groups():
    """route_v1 C059 實證:兩次住院相隔 >14 日各有獨立限額,LLM 每組一行是對的,合併會把 102,000 砍成 82,000。"""
    lines = [_capped("第10條", 50000, 60000), _capped("第10條", 30000, 60000)]
    assert calc.normalize_calculation(lines, groups=2) == lines
    assert len(calc.normalize_calculation(lines, groups=1)) == 1
    bundle = ClaimBundle.model_validate(_bundle_payload("C059"))
    d = _decision_payload("C059", "POL-C", "第10條")["adjudication"]
    d["calculation"] = [json.loads(ln.model_dump_json()) for ln in lines]
    adj = finalize_adjudication(AdjudicationDraft.model_validate(d), bundle=bundle, merge_split_lines=True)
    assert len(adj.calculation) == 2 and adj.approved_amount == Decimal(80000)
