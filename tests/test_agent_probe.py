"""SPEC-n:tools/agent_probe.py——Foundry Agent Service 探針(Agents v2 + Responses),以 FakeResponses 為邊界。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.gate.checks import run_gate
from app.gate.policy_index import load_policy_index
from app.pipeline import intake, runner
from app.pipeline.context import build_policy_context
from app.schemas.claim import AdjudicationDraft, ClaimBundle, DecisionResult, to_structured_output_schema
from tests.test_pipeline import CLAIMS, FakeAOAI, FakeDI, _bundle_payload, _decision_payload
from tools import agent_probe as ap

INDEX = load_policy_index()


def _bundle(cid: str = "C001") -> ClaimBundle:
    return ClaimBundle.model_validate(_bundle_payload(cid))


def _intake(cid: str = "C001"):
    status, missing = intake.check_documents(CLAIMS / cid)
    return intake.intake_result(cid, status, missing, "POL-A", INDEX)


def _decision_json(cid: str = "C001") -> str:
    return json.dumps(_decision_payload(cid, "POL-A", "第7條"), ensure_ascii=False)


def _final(rid: str, text: str, cached: int | None = 40) -> SimpleNamespace:
    details = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    return SimpleNamespace(id=rid, output=[SimpleNamespace(type="message")], output_text=text,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=10, input_tokens_details=details))


def _tool_call(rid: str, call_id: str, arguments: dict[str, Any]) -> SimpleNamespace:
    item = SimpleNamespace(type="function_call", call_id=call_id, name=ap.GATE_TOOL_NAME,
                           arguments=json.dumps(arguments, ensure_ascii=False))
    return SimpleNamespace(id=rid, output=[item], output_text="",
                           usage=SimpleNamespace(input_tokens=100, output_tokens=10,
                                                 input_tokens_details=SimpleNamespace(cached_tokens=0)))


class FakeResponses:
    """依序回傳預先排好的 response;記錄每次 create 的 kwargs。"""

    def __init__(self, queue: list[Any]):
        self._queue, self.calls = list(queue), []
        self.responses = self

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return self._queue.pop(0)


# ---------- N0:runner 純重構——判定訊息抽成函式,run_case 使用同一份 ----------
def test_n0_run_case_uses_build_decision_messages(tmp_path):
    cid = "C001"
    fake = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    runner.run_case(CLAIMS / cid, "full", FakeDI(CLAIMS / cid), fake, None, INDEX, tmp_path)
    clauses = build_policy_context("POL-A", "full", INDEX)
    system, user = runner.build_decision_messages(_bundle(cid), _intake(cid), clauses)
    decision_call = fake.calls[1]
    assert decision_call["schema_name"] == "DecisionResult"
    assert decision_call["system"] == system and decision_call["user"] == user


# ---------- N1/N2:agent 定義 ----------
def test_n1_definition_without_tool_pins_model_and_strict_schema():
    d = ap.build_agent_definition("gpt-4.1-mini", "指示文", tool=None)
    assert d.model == "gpt-4.1-mini" and d.instructions == "指示文"
    fmt = d.text.format
    assert fmt.type == "json_schema" and fmt.strict is True and fmt.name == "DecisionResult"
    expected = to_structured_output_schema(DecisionResult)
    assert json.dumps(fmt.schema, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert not d.tools


def test_n2_definition_with_gate_tool_declares_one_strict_function():
    d = ap.build_agent_definition("gpt-4.1-mini", "指示文", tool="gate")
    assert len(d.tools) == 1
    t = d.tools[0]
    assert t.type == "function" and t.name == ap.GATE_TOOL_NAME and t.strict is True
    expected = to_structured_output_schema(AdjudicationDraft)
    assert json.dumps(t.parameters, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert ap.GATE_TOOL_NAME in d.instructions  # 工具使用說明附在指示文後


# ---------- N3–N5:回應解析 ----------
def test_n3_parse_final_message_returns_decision():
    p = ap.parse_response(_final("r1", _decision_json()), "C001")
    assert p.kind == "final" and isinstance(p.result, DecisionResult) and p.result.adjudication.claim_id == "C001"


def test_n4_parse_function_call_returns_call_id_and_arguments():
    draft = _decision_payload("C001", "POL-A", "第7條")["adjudication"]
    p = ap.parse_response(_tool_call("r1", "call_9", draft), "C001")
    assert p.kind == "tool_call" and p.call_id == "call_9" and p.arguments == draft


def test_n5_parse_invalid_json_raises_probe_error_naming_case():
    with pytest.raises(ap.ProbeError) as ei:
        ap.parse_response(_final("r1", '{"policy": {}}'), "C007")
    assert "C007" in str(ei.value)


# ---------- N6/N7:工具迴圈 ----------
def test_n6_tool_loop_round_trips_gate_output_with_previous_response_id():
    draft = _decision_payload("C001", "POL-A", "第7條")["adjudication"]
    fake = FakeResponses([_tool_call("r1", "call_1", draft), _final("r2", _decision_json())])
    seen: list[dict[str, Any]] = []

    def gate_fn(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(args)
        return {"passed": False, "flags": [{"check": "x", "detail": "y"}]}

    res = ap.tool_loop(fake, {"name": "a", "type": "agent_reference"}, "輸入", gate_fn, "C001", max_rounds=2)
    assert res.kind == "final" and res.rounds == 1 and seen == [draft]
    assert res.flags_before == [{"check": "x", "detail": "y"}]
    second = fake.calls[1]
    assert second["previous_response_id"] == "r1"
    assert second["input"] == [{"type": "function_call_output", "call_id": "call_1",
                                "output": json.dumps(gate_fn(draft), ensure_ascii=False)}]
    assert fake.calls[0]["extra_body"] == {"agent_reference": {"name": "a", "type": "agent_reference"}}


def test_n7_tool_loop_stops_after_max_rounds_without_third_call():
    draft = _decision_payload("C001", "POL-A", "第7條")["adjudication"]
    fake = FakeResponses([_tool_call(f"r{i}", f"c{i}", draft) for i in range(1, 4)])
    n = {"calls": 0}

    def gate_fn(args: dict[str, Any]) -> dict[str, Any]:
        n["calls"] += 1
        return {"passed": False, "flags": []}

    res = ap.tool_loop(fake, {"name": "a", "type": "agent_reference"}, "輸入", gate_fn, "C001", max_rounds=2)
    assert res.kind == "exhausted" and res.rounds == 2 and n["calls"] == 2 and len(fake.calls) == 3
    assert len(fake._queue) == 0  # 剛好消耗 3 個:初始 + 2 輪回填,無第 4 次


# ---------- N8:usage ----------
def test_n8_usage_record_reads_cached_and_tolerates_missing_details():
    full = {"input_tokens": 100, "output_tokens": 10, "cached_tokens": 40}
    assert ap.usage_record(_final("r", "{}", cached=40)) == full
    assert ap.usage_record(_final("r", "{}", cached=None))["cached_tokens"] == 0


# ---------- N9:比對 ----------
def test_n9_compare_flags_each_field_and_identical_only_when_all_equal():
    a = DecisionResult.model_validate_json(_decision_json())
    same = ap.compare(a, a)
    assert same == {"decision": True, "approved_amount": True, "calculation_rows": True, "citations": True,
                    "identical": True}
    b_payload = _decision_payload("C001", "POL-A", "第7條")
    b_payload["adjudication"]["citations"][0]["quote"] = "改過的引文"
    b = DecisionResult.model_validate(b_payload)
    diff = ap.compare(a, b)
    assert diff["citations"] is False and diff["identical"] is False and diff["decision"] is True


# ---------- N10:gate_fn 與 run_gate 同源 ----------
def test_n10_gate_fn_matches_run_gate_on_same_draft():
    bundle = _bundle()
    draft = _decision_payload("C001", "POL-A", "第7條")["adjudication"]
    out = ap.make_gate_fn(bundle, INDEX)(draft)
    expected = run_gate(runner.finalize_adjudication(AdjudicationDraft.model_validate(draft)), bundle, INDEX)
    assert out == {"passed": expected.passed,
                   "flags": [{"check": f.check, "detail": f.detail} for f in expected.flags]}
    assert set(out) == {"passed", "flags"}


# ---------- N11–N13:CLI(以假 project client / 假 AOAI 跑 main) ----------
class FakeAgents:
    def __init__(self):
        self.created: list[tuple[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []

    def create_version(self, name: str, *, definition: Any, description: str = "") -> SimpleNamespace:
        self.created.append((name, definition))
        return SimpleNamespace(name=name, version="1")

    def delete_version(self, name: str, version: str) -> None:
        self.deleted.append((name, version))


class FakeProject:
    def __init__(self, responses: FakeResponses):
        self.agents, self._responses = FakeAgents(), responses

    def get_openai_client(self) -> FakeResponses:
        return self._responses


def _pred_dir(tmp_path, cids=("C001",)):
    for cid in cids:
        payload = json.dumps(_bundle_payload(cid), ensure_ascii=False)
        (tmp_path / f"{cid}.bundle.json").write_text(payload, encoding="utf-8")
    return tmp_path


def test_n11_parity_mode_writes_direct_and_agent_decisions_with_compare(tmp_path, monkeypatch):
    fake_resp = FakeResponses([_final("r1", _decision_json())])
    project = FakeProject(fake_resp)
    monkeypatch.setattr(ap, "make_project_client", lambda endpoint: project)
    direct = FakeAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"))
    monkeypatch.setattr(ap, "AOAIClient", lambda: direct)
    out = tmp_path / "o.jsonl"
    rc = ap.main([str(_pred_dir(tmp_path)), "--limit", "1", "--mode", "parity", "--endpoint", "https://x/api/projects/p",
                  "--deployment", "gpt-4.1-mini", "--out", str(out)])
    assert rc == 0
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["claim_id"] == "C001" and row["valid"] is True and row["kind"] == "final"
    assert row["compare"]["identical"] is True and row["direct_decision"]["adjudication"]["claim_id"] == "C001"
    assert row["agent_usage"] == [{"input_tokens": 100, "output_tokens": 10, "cached_tokens": 40}]
    assert "latency_s" in row and row["agent"]["name"].startswith("claimcrew-probe-pol-a-parity-")
    # agent 定義:parity 模式無工具、instructions 與 run_case 的 system 同源
    name, definition = project.agents.created[0]
    assert not definition.tools and definition.model == "gpt-4.1-mini"
    assert fake_resp.calls[0]["input"] == json.loads(json.dumps(fake_resp.calls[0]["input"]))  # user 為 JSON 字串
    assert project.agents.deleted == [(name, "1")]  # 預設清理


def test_n12_gate_mode_records_rounds_and_flags_before_after(tmp_path, monkeypatch):
    draft = _decision_payload("C001", "POL-A", "第7條")["adjudication"]
    fake_resp = FakeResponses([_tool_call("r1", "c1", draft), _final("r2", _decision_json())])
    project = FakeProject(fake_resp)
    monkeypatch.setattr(ap, "make_project_client", lambda endpoint: project)
    out = tmp_path / "o.jsonl"
    rc = ap.main([str(_pred_dir(tmp_path)), "--mode", "gate", "--endpoint", "https://x/api/projects/p",
                  "--out", str(out), "--keep-agents"])
    assert rc == 0
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["rounds"] == 1 and isinstance(row["flags_before"], list) and isinstance(row["flags_after"], list)
    assert "direct_decision" not in row and "compare" not in row
    assert project.agents.created[0][1].tools[0].name == ap.GATE_TOOL_NAME
    assert project.agents.deleted == []  # --keep-agents


def test_n13_summary_counts_valid_identical_cached_and_gate_improvement():
    rows = [
        {"valid": True, "compare": {"identical": True}, "agent_usage": [{"cached_tokens": 0}]},
        {"valid": True, "compare": {"identical": False}, "agent_usage": [{"cached_tokens": 30}]},
        {"valid": False, "agent_usage": [{"cached_tokens": 0}]},
    ]
    s = ap.summarize(rows, "parity")
    assert "valid 2/3" in s and "identical 1/3" in s and "cached_tokens>0:1/2" in s
    flag = [{"check": "a"}]
    g = [
        {"valid": True, "kind": "final", "rounds": 1, "flags_before": flag, "flags_after": [], "agent_usage": []},
        {"valid": True, "kind": "final", "rounds": 1, "flags_before": flag, "flags_after": flag,
         "agent_usage": []},  # 未改善
        {"valid": True, "kind": "final", "rounds": 0, "flags_before": None, "flags_after": [], "agent_usage": []},
        {"valid": False, "kind": "exhausted", "rounds": 2, "flags_before": flag, "agent_usage": []},
    ]
    t = ap.summarize(g, "gate")
    assert "有旗標案 3,flags_after < flags_before:1" in t and "exhausted 1" in t and "[0, 1, 1, 2]" in t


def test_n14_openai_error_on_one_case_is_recorded_not_fatal(tmp_path, monkeypatch):
    import openai

    class Boom(FakeResponses):
        def create(self, **kw: Any) -> Any:
            raise openai.OpenAIError("400 simulated")

    project = FakeProject(Boom([]))
    monkeypatch.setattr(ap, "make_project_client", lambda endpoint: project)
    out = tmp_path / "o.jsonl"
    rc = ap.main([str(_pred_dir(tmp_path)), "--mode", "gate", "--endpoint", "https://x/api/projects/p",
                  "--out", str(out)])
    assert rc == 0
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["kind"] == "error" and row["valid"] is False and "400 simulated" in row["error"]
    assert project.agents.deleted  # 例外後仍清理


def test_n15_default_agent_ref_style_is_agent_reference():
    body = ap.agent_ref_body("a", "1")
    assert body == {"agent_reference": {"name": "a", "type": "agent_reference", "version": "1"}}


def test_n16_direct_arm_value_error_is_recorded_not_fatal(tmp_path, monkeypatch):
    fake_resp = FakeResponses([_final("r1", _decision_json())])
    project = FakeProject(fake_resp)
    monkeypatch.setattr(ap, "make_project_client", lambda endpoint: project)

    class Truncated:
        def structured_call(self, **kw: Any) -> Any:
            raise ValueError("finish_reason=length simulated")

    monkeypatch.setattr(ap, "AOAIClient", lambda: Truncated())
    out = tmp_path / "o.jsonl"
    rc = ap.main([str(_pred_dir(tmp_path)), "--mode", "parity", "--endpoint", "https://x/api/projects/p",
                  "--out", str(out)])
    assert rc == 0
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["kind"] == "error" and "finish_reason=length" in row["error"]
    assert row["agent_decision"]["adjudication"]["claim_id"] == "C001"  # agent 臂結果仍保留


def test_n17_parallel_function_calls_are_all_answered_in_one_round():
    """2026-10-03 實證:agent 一次回兩個 function_call,只回填第一個 → 400 'No tool output found for function call'。"""
    draft = _decision_payload("C001", "POL-A", "第7條")["adjudication"]
    item = lambda cid: SimpleNamespace(type="function_call", call_id=cid, name=ap.GATE_TOOL_NAME,  # noqa: E731
                                       arguments=json.dumps(draft, ensure_ascii=False))
    two = SimpleNamespace(id="r1", output=[item("c1"), item("c2")], output_text="",
                          usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                                input_tokens_details=SimpleNamespace(cached_tokens=0)))
    fake = FakeResponses([two, _final("r2", _decision_json())])
    n = {"calls": 0}

    def gate_fn(args: dict[str, Any]) -> dict[str, Any]:
        n["calls"] += 1
        return {"passed": True, "flags": []}

    res = ap.tool_loop(fake, {"name": "a"}, "輸入", gate_fn, "C001", max_rounds=2)
    assert res.kind == "final" and res.rounds == 1 and n["calls"] == 2
    outputs = fake.calls[1]["input"]
    assert [o["call_id"] for o in outputs] == ["c1", "c2"] and all(o["type"] == "function_call_output" for o in outputs)


def test_n18_final_text_with_trailing_second_json_object_parses_first():
    """2026-10-03 R2 實證:平行回填後 output_text 為兩個 JSON 物件相接 → json_invalid trailing characters。"""
    text = _decision_json() + _decision_json()
    p = ap.parse_response(_final("r1", text), "C001")
    assert p.kind == "final" and p.result is not None and p.result.adjudication.claim_id == "C001"
