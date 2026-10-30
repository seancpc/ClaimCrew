"""SPEC-o:app/pipeline/repair.py——Repair agent 編排(觸發判定、修補訊息、守門、fail-closed)。

邊界 mock:Agent Service 用戶端。"""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.gate.checks import routes_to_manual, run_gate
from app.gate.policy_index import load_policy_index
from app.pipeline import repair as rp
from app.pipeline.agent_service import GATE_TOOL_NAME, AgentServiceError
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import (
    AdjudicationDraft,
    AdjudicationResult,
    ClaimBundle,
    ClaimDecision,
    DecisionResult,
    GateFlag,
    GateResult,
    PipelineOutput,
)
from tests.test_pipeline import _bundle_payload, _calc_line, _decision_payload

INDEX = load_policy_index()


# ---------- 夾具 ----------
def _bundle(cid: str = "C001") -> ClaimBundle:
    return ClaimBundle.model_validate(_bundle_payload(cid))


def _adj(cid: str = "C001", **over: Any) -> AdjudicationResult:
    d = _decision_payload(cid, "POL-A", "第7條")["adjudication"]
    d.update(over)
    return finalize_adjudication(AdjudicationDraft.model_validate(d))


def _line(clause: str, qty: int, unit: int) -> dict[str, Any]:
    ln = _calc_line(clause)
    ln.update(quantity=qty, unit_amount=str(unit))
    return ln


def _cit(clause: str) -> dict[str, Any]:
    return {"policy_id": "POL-A", "clause_no": clause, "quote": INDEX["POL-A"][clause].text[:20], "relevance": "test"}


def _good_adj(cid: str = "C001", **over: Any) -> AdjudicationResult:
    """能通過真實 Gate 的 C001 核付判定(與真值同:第7條 2000×6 + 第11條 1000×6 = 18,000)。"""
    return _adj(cid, calculation=[_line("第7條", 6, 2000), _line("第11條", 6, 1000)],
                citations=[_cit("第7條"), _cit("第11條")], **over)


def _gate(adj: AdjudicationResult, flags: list[tuple[str, str]], final: str | None = None) -> GateResult:
    fl = [GateFlag(check=c, detail=d) for c, d in flags]
    fd = ClaimDecision(final) if final else (ClaimDecision.MANUAL_REVIEW if routes_to_manual(fl) else adj.decision)
    return GateResult(claim_id=adj.claim_id, original_decision=adj.decision, final_decision=fd, flags=fl, passed=not fl)


def _output(adj: AdjudicationResult, flags: list[tuple[str, str]]) -> PipelineOutput:
    return PipelineOutput(claim_id=adj.claim_id, adjudication=adj, gate=_gate(adj, flags))


def _final(rid: str, decision: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=rid, output=[SimpleNamespace(type="message")],
                           output_text=json.dumps(decision, ensure_ascii=False),
                           usage=SimpleNamespace(input_tokens=100, output_tokens=10,
                                                 input_tokens_details=SimpleNamespace(cached_tokens=40)))


def _tool_call(rid: str, call_id: str, draft: dict[str, Any]) -> SimpleNamespace:
    item = SimpleNamespace(type="function_call", call_id=call_id, name=GATE_TOOL_NAME,
                           arguments=json.dumps(draft, ensure_ascii=False))
    return SimpleNamespace(id=rid, output=[item], output_text="",
                           usage=SimpleNamespace(input_tokens=100, output_tokens=10,
                                                 input_tokens_details=SimpleNamespace(cached_tokens=0)))


class FakeAgent:
    def __init__(self, queue: list[Any]):
        self._queue, self.calls = list(queue), []
        self.responses = self

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if not self._queue:
            raise AssertionError("agent 被多呼叫了一次")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


REF = {"name": "claimcrew-repair-pol-a", "version": "1"}
CIT = ("citation_quote_mismatch", "POL-A 第7條 引文與原文不符:…")
CALC11 = ("calc_structure_mismatch", "POL-A 第7/8條(日額)與第11條(輔助金)必須成對出現(第11條依第7條住院日數給付)")
WAIT = ("waiting_period_unmet", "生效日 <日期> 至事件日 <日期> 僅 18 日,未滿 30 日疾病等待期")
ADV = ("unlisted_surgery_review", "治療摘要未含附錄一任一手術名")


# ---------- O1 觸發判定 ----------
def test_o1_plan_repair_classifies_flags():
    adj = _adj()
    assert rp.plan_repair(_gate(adj, [ADV])).kind == "skip"
    assert rp.plan_repair(_gate(adj, [CALC11, ADV])).kind == "skip"
    assert rp.plan_repair(_gate(adj, [("low_confidence_fields", "x")])).kind == "skip"
    assert rp.plan_repair(_gate(adj, [CIT, ("citation_not_found", "y")])).kind == "citation_only"
    p = rp.plan_repair(_gate(adj, [CALC11]))
    assert p.kind == "repairable" and p.flagged_clauses == frozenset({"第7條", "第8條", "第11條"})
    assert rp.plan_repair(_gate(adj, [WAIT])).kind == "repairable"
    assert rp.plan_repair(_gate(adj, [CIT, CALC11])).kind == "repairable"  # 混合:非純引文
    assert rp.plan_repair(_gate(adj, [])).kind == "skip"  # 無旗標不修


def test_o1_flagged_clause_nos_handles_slash_lists_and_appendix():
    flags = [GateFlag(check="x", detail="POL-C 附錄一 引文與原文不符"),
             GateFlag(check="y", detail="第9條 拆 2 行合計 22000 超過限額")]
    assert rp.flagged_clause_nos(flags) == frozenset({"附錄一", "第9條"})


# ---------- O2 修補訊息 ----------
def test_o2_repair_messages_extend_decision_system_and_carry_truncated_flags():
    from app.pipeline.runner import build_decision_messages
    bundle = _bundle()
    code, ir, clauses = rp.decision_context(bundle, INDEX)
    system, _ = build_decision_messages(bundle, ir, clauses)
    adj = _adj()
    long_flag = GateFlag(check="calc_reference_mismatch", detail="第7條 " + "x" * 500)
    instr, user = rp.build_repair_messages(bundle, ir, clauses, adj, [long_flag])
    assert instr.startswith(system) and "detail" in instr and "不得新增" in instr and "confidence" in instr
    # R2-5:「收據」「不得更動」字樣既有(system 與 ②),須比完整句才非空斷言
    assert "actual_amount)與他保扣除額(deduction)為事實,不得更動" in instr
    payload = json.loads(user)
    assert set(payload) == {"intake", "claim_bundle", "derived_facts", "original_decision", "gate_flags"}
    assert payload["original_decision"]["claim_id"] == "C001"
    assert len(payload["gate_flags"][0]["detail"]) == 200
    assert payload["gate_flags"][0]["check"] == "calc_reference_mismatch"


# ---------- O3–O8 守門 ----------
def _plan(kind: str, clauses: frozenset[str] = frozenset()) -> rp.RepairPlan:
    return rp.RepairPlan(kind=kind, flagged_clauses=clauses)


def _g(orig: AdjudicationResult, rep: AdjudicationResult, plan: rp.RepairPlan, flags=(), so=lambda: None) -> str:
    return rp.apply_guards(orig, rep, plan, _gate(rep, list(flags)), so)


def test_o3_gate_flags_after_repair_block():
    adj = _adj()
    reason = _g(adj, adj, _plan("repairable", frozenset({"第7條"})), [CALC11])
    assert reason == "gate_flags"


def test_o4_upgrade_to_approve_blocked():
    orig = _adj(decision="deny", calculation=[])
    repaired = _adj()
    assert _g(orig, repaired, _plan("repairable", frozenset({"第7條"}))) == "upgrade_blocked"
    orig2 = _adj()
    denied = _adj(decision="deny", calculation=[])
    assert _g(orig2, denied, _plan("repairable", frozenset({"第7條"}))) == "released"


def test_o5_new_line_must_be_flagged_and_existing_unflagged_lines_frozen():
    orig = _adj()
    line9 = _calc_line("第9條")
    with_9 = _adj(calculation=[_calc_line("第7條"), line9])
    assert _g(orig, with_9, _plan("repairable", frozenset({"第11條"}))) == "unflagged_new_line"
    with_11 = _adj(calculation=[_calc_line("第7條"), _calc_line("第11條")])
    assert _g(orig, with_11, _plan("repairable", frozenset({"第11條"}))) == "released"
    # pre-mortem:旗標只點第 11 條,第 7 條既有行被動了 → 擋
    changed7 = _calc_line("第7條")
    changed7["quantity"] = 2
    tampered = _adj(calculation=[changed7, _calc_line("第11條")])
    assert _g(orig, tampered, _plan("repairable", frozenset({"第11條"}))) == "unflagged_line_changed"
    # 刪掉未點名的既有行同樣擋(覆蓋率抓到的未測路徑)
    dropped7 = _adj(calculation=[_calc_line("第11條")])
    assert _g(orig, dropped7, _plan("repairable", frozenset({"第11條"}))) == "unflagged_line_changed"
    # 旗標點名的行被刪則放行到下一關(G6 以後)
    dropped_flagged = _adj(calculation=[])
    assert _g(orig, dropped_flagged, _plan("repairable", frozenset({"第7條"}))) == "released"


def test_o5b_downgrade_to_deny_clears_lines_without_tripping_line_guard():
    """C004 實證(2026-10-03):等待期旗標 detail 無條號,agent 改 deny 清空明細 → 不得被 G3 擋。"""
    orig = _adj()
    denied = _adj(decision="deny", calculation=[])
    assert _g(orig, denied, _plan("repairable", frozenset())) == "released"
    to_manual = _adj(decision="manual_review", calculation=[])
    assert _g(orig, to_manual, _plan("repairable", frozenset())) == "released"


def _capped(clause: str, actual: int, cap: int, deduction: int = 0) -> dict[str, Any]:
    ln = _calc_line(clause)
    ln.update(kind="capped_actual", quantity=0, unit_amount="0", actual_amount=str(actual), cap=str(cap),
              deduction=str(deduction))
    return ln


def test_r2_g7_receipt_facts_cannot_shrink_even_on_flagged_clause():
    """C003 route_v1 實證(2026-10-03):旗標點名第 9 條,Repair 把 actual 22,000 改 18,000 湊限額 → 必須擋。"""
    orig = _adj(calculation=[_capped("第9條", 18000, 20000), _capped("第9條", 4000, 20000)])
    plan = _plan("repairable", frozenset({"第9條"}))
    shrunk = _adj(calculation=[_capped("第9條", 18000, 20000)])
    assert _g(orig, shrunk, plan) == "facts_shrunk"
    merged = _adj(calculation=[_capped("第9條", 22000, 20000)])  # Σactual 不變,合併為一行
    assert _g(orig, merged, plan) == "released"
    grown = _adj(calculation=[_capped("第9條", 22000, 20000), _capped("第9條", 1000, 20000)])  # Σ 變多不擋(由 Gate 管)
    assert _g(orig, grown, plan) == "released"


def test_r2_g7_deduction_must_stay_equal():
    orig = _adj(calculation=[_capped("第9條", 22000, 20000, deduction=1000)])
    plan = _plan("repairable", frozenset({"第9條"}))
    less_deduction = _adj(calculation=[_capped("第9條", 22000, 20000, deduction=0)])  # 少扣 = 多付
    assert _g(orig, less_deduction, plan) == "facts_shrunk"
    more_deduction = _adj(calculation=[_capped("第9條", 22000, 20000, deduction=2000)])
    assert _g(orig, more_deduction, plan) == "facts_shrunk"
    same = _adj(calculation=[_capped("第9條", 22000, 20000, deduction=1000)])
    assert _g(orig, same, plan) == "released"


def test_r2_g7_not_applied_when_downgraded_and_ordered_before_confidence():
    orig = _adj(calculation=[_capped("第9條", 22000, 20000)], confidence=0.7)
    denied = _adj(decision="deny", calculation=[], confidence=0.7)
    assert _g(orig, denied, _plan("repairable", frozenset({"第9條"}))) == "released"
    shrunk_and_overconfident = _adj(calculation=[_capped("第9條", 18000, 20000)], confidence=0.9)
    assert _g(orig, shrunk_and_overconfident, _plan("repairable", frozenset({"第9條"}))) == "facts_shrunk"  # G7 先於 G6


def test_o6_confidence_cannot_rise():
    orig = _adj(confidence=0.7)
    up = _adj(confidence=0.9)
    down = _adj(confidence=0.6)
    plan = _plan("repairable", frozenset({"第7條"}))
    assert _g(orig, up, plan) == "confidence_raised"
    assert _g(orig, down, plan) == "released"


def test_o7_citation_only_requires_same_decision_and_second_opinion_agreement():
    orig = _adj()
    plan = _plan("citation_only")
    calc_changed = _adj(calculation=[_calc_line("第7條"), _calc_line("第7條")])
    assert _g(orig, calc_changed, plan) == "citation_only_changed"
    quote_only = _adj()
    cit = quote_only.citations[0].model_copy(update={"quote": INDEX["POL-A"]["第7條"].text[:30]})
    quote_only = quote_only.model_copy(update={"citations": [cit]})
    calls = {"n": 0}

    def agree() -> AdjudicationResult:
        calls["n"] += 1
        return _adj()

    assert _g(orig, quote_only, plan, so=agree) == "released" and calls["n"] == 1

    def disagree() -> AdjudicationResult:
        return _adj(calculation=[_calc_line("第7條"), _calc_line("第7條")])  # 金額 2000 ≠ 1000

    assert _g(orig, quote_only, plan, so=disagree) == "second_opinion_mismatch"


def test_o7b_second_opinion_not_called_when_earlier_guard_fails_or_plan_repairable():
    orig = _adj()
    called = {"n": 0}

    def so() -> AdjudicationResult:
        called["n"] += 1
        return _adj()

    rp.apply_guards(orig, orig, _plan("citation_only"), _gate(orig, [CIT]), so)  # G1 先擋
    _g(orig, orig, _plan("repairable", frozenset({"第7條"})), so=so)  # repairable 不問第二意見
    assert called["n"] == 0


# ---------- O8/O9/O10/O14 repair_case 編排 ----------
def _decision_json(adj: AdjudicationResult) -> dict[str, Any]:
    d = _decision_payload(adj.claim_id, "POL-A", "第7條")
    draft = AdjudicationDraft.model_validate(adj.model_dump(exclude={"approved_amount", "undisputed_amount"}))
    d["adjudication"] = json.loads(draft.model_dump_json())
    return d


def test_o8_released_path_uses_repaired_adjudication_and_records():
    bundle = _bundle()
    orig = _adj(calculation=[_line("第7條", 6, 2000)], citations=[_cit("第7條")], confidence=0.9)  # 漏第 11 條
    out = _output(orig, [CALC11])
    repaired = _good_adj(confidence=0.9)
    assert run_gate(repaired, bundle, INDEX).passed  # 夾具前提:修補結果本身過 Gate
    agent = FakeAgent([_tool_call("r1", "c1", _decision_json(repaired)["adjudication"]),
                       _final("r2", _decision_json(repaired))])
    res = rp.repair_case(out, bundle, INDEX, agent, REF, second_opinion_fn=lambda: orig, advisory=False)
    assert res.repair is not None and res.repair.guard_reason == "released"
    assert res.adjudication == repaired and res.gate.passed and res.gate.final_decision is ClaimDecision.APPROVE
    assert res.repair.invoked and res.repair.rounds == 1 and res.repair.plan == "repairable"
    assert [f.check for f in res.repair.flags_before] == ["calc_structure_mismatch"] and res.repair.flags_after == []
    assert res.repair.pre_repair_amount == Decimal(12000) and res.repair.repaired_amount == Decimal(18000)
    assert len(res.repair.usage) == 2 and res.repair.usage[0]["input_tokens"] == 100


def test_o14_guard_reason_becomes_routing_flag_on_original_gate():
    bundle = _bundle()
    orig = _adj(decision="deny", calculation=[])
    out = _output(orig, [WAIT])
    upgraded = _good_adj()
    agent = FakeAgent([_final("r1", _decision_json(upgraded))])
    res = rp.repair_case(out, bundle, INDEX, agent, REF, second_opinion_fn=lambda: orig, advisory=False)
    assert res.adjudication == orig and res.gate.final_decision is ClaimDecision.MANUAL_REVIEW
    checks = [f.check for f in res.gate.flags]
    assert checks[:1] == ["waiting_period_unmet"] and checks[-1] == "repair_upgrade_blocked"
    assert routes_to_manual(res.gate.flags) and res.repair is not None and res.repair.guard_reason == "upgrade_blocked"


def test_o9_fail_closed_on_agent_error_keeps_original_gate_and_skips_second_opinion():
    import openai
    bundle = _bundle()
    orig = _adj()
    out = _output(orig, [CIT])
    called = {"n": 0}

    def so() -> AdjudicationResult:
        called["n"] += 1
        return orig

    for exc in (openai.OpenAIError("429 simulated"), AgentServiceError("C001:驗證失敗")):
        res = rp.repair_case(out, bundle, INDEX, FakeAgent([exc]), REF, second_opinion_fn=so)
        assert res.adjudication == orig and res.gate == out.gate
        assert res.repair is not None and res.repair.invoked and res.repair.error and res.repair.guard_reason == "error"
    assert called["n"] == 0


def test_o10_exhausted_loop_routes_manual_with_reason():
    bundle = _bundle()
    orig = _adj()
    out = _output(orig, [CALC11])
    draft = _decision_json(orig)["adjudication"]
    agent = FakeAgent([_tool_call(f"r{i}", f"c{i}", draft) for i in range(1, 4)])
    res = rp.repair_case(out, bundle, INDEX, agent, REF, second_opinion_fn=lambda: orig, advisory=False)
    assert res.repair is not None and res.repair.guard_reason == "exhausted" and res.repair.rounds == 2
    assert res.gate.final_decision is ClaimDecision.MANUAL_REVIEW and res.gate.flags[-1].check == "repair_exhausted"
    assert len(agent.calls) == 3


def test_o1b_skip_and_passed_do_not_call_agent():
    bundle = _bundle()
    orig = _adj()
    agent = FakeAgent([])
    for out in (_output(orig, [ADV]), _output(orig, [])):
        res = rp.repair_case(out, bundle, INDEX, agent, REF, second_opinion_fn=lambda: orig)
        assert res.repair is not None and res.repair.invoked is False
        assert res.adjudication == orig and res.gate == out.gate
    assert agent.calls == []


# ---------- O11 schema 相容 ----------
def test_o11_old_output_json_without_repair_loads_and_llm_schema_unchanged():
    from app.schemas.claim import to_structured_output_schema
    orig = _adj()
    old = json.loads(_output(orig, []).model_dump_json())
    old.pop("repair", None)
    loaded = PipelineOutput.model_validate(old)
    assert loaded.repair is None
    s = json.dumps(to_structured_output_schema(DecisionResult), sort_keys=True)
    assert "repair" not in s and "RepairRecord" not in s


def test_o11b_repair_record_round_trips():
    orig = _adj()
    out = _output(orig, [CIT])
    res = rp.repair_case(out, _bundle(), INDEX, FakeAgent([]), REF, second_opinion_fn=lambda: orig) if False else None
    rec = rp.RepairRecord(invoked=False, plan="skip", rounds=0, flags_before=[], flags_after=[], guard_reason="skipped",
                          pre_repair_decision=orig.decision, pre_repair_amount=orig.approved_amount, usage=[])
    po = PipelineOutput(claim_id="C001", adjudication=orig, gate=out.gate, repair=rec)
    assert PipelineOutput.model_validate_json(po.model_dump_json()).repair == rec
    assert res is None
