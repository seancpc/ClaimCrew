"""SPEC-o O12/O13:tools/rerepair.py 離線重放與 run_pipeline --repair 串接(FakeProject / FakeAOAI 為邊界)。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.gate.policy_index import load_policy_index
from app.pipeline import runner
from app.schemas.claim import PipelineOutput, RepairRecord
from tests.test_agent_probe import FakeProject, FakeResponses
from tests.test_pipeline import CLAIMS, FakeAOAI, FakeDI, _bundle_payload, _decision_payload
from tests.test_repair import ADV, CALC11, _adj, _cit, _decision_json, _final, _good_adj, _line, _output
from tools import rerepair as rr

INDEX = load_policy_index()


def _pred_dir(tmp_path: Path) -> Path:
    """三案:C001 可修(calc 旗標)、C002 提醒型(skip)、C003 無旗標;每案都有 bundle。"""
    d = tmp_path / "pred"
    d.mkdir()
    cases = {
        "C001": _output(_adj("C001", calculation=[_line("第7條", 6, 2000)], citations=[_cit("第7條")]), [CALC11]),
        "C002": _output(_adj("C002"), [ADV]),
        "C003": _output(_adj("C003"), []),
    }
    for cid, out in cases.items():
        (d / f"{cid}.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
        (d / f"{cid}.bundle.json").write_text(json.dumps(_bundle_payload(cid), ensure_ascii=False), encoding="utf-8")
    return d


def test_o12_rerepair_invokes_only_repairable_cases_and_writes_full_out_dir(tmp_path, monkeypatch, capsys):
    pred = _pred_dir(tmp_path)
    repaired = _good_adj("C001")
    fake = FakeResponses([_final("r1", _decision_json(repaired))])
    project = FakeProject(fake)
    monkeypatch.setattr(rr, "make_project_client", lambda endpoint: project)
    direct = FakeAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"))
    monkeypatch.setattr(rr, "AOAIClient", lambda: direct)
    out_dir = tmp_path / "out"
    rc = rr.main([str(pred), "--out", str(out_dir), "--endpoint", "https://x/api/projects/p", "--release"])  # 量測路徑
    assert rc == 0
    names = sorted(p.name for p in out_dir.glob("C*.json") if not p.name.endswith(".bundle.json"))
    assert names == ["C001.json", "C002.json", "C003.json"]
    o1 = PipelineOutput.model_validate_json((out_dir / "C001.json").read_text(encoding="utf-8"))
    o2 = PipelineOutput.model_validate_json((out_dir / "C002.json").read_text(encoding="utf-8"))
    o3 = PipelineOutput.model_validate_json((out_dir / "C003.json").read_text(encoding="utf-8"))
    assert o1.repair is not None and o1.repair.invoked and o1.repair.guard_reason == "released" and o1.gate.passed
    assert o2.repair is not None and o2.repair.invoked is False and o2.repair.plan == "skip"
    assert o3.repair is not None and o3.repair.invoked is False
    assert len(fake.calls) == 1  # 只有 C001 呼叫 agent
    assert (out_dir / "C001.bundle.json").exists()  # bundle 一併複製,供 regate / 後續重放
    summary = capsys.readouterr().out
    assert "invoked 1" in summary and "released 1" in summary and "skip 2" in summary
    # eval 可直接吃 out 目錄
    from eval.run_eval import load_predictions
    assert set(load_predictions(out_dir, ["C001", "C002", "C003"])) == {"C001", "C002", "C003"}
    # 預設清理 agent 版本
    assert project.agents.deleted


def test_o12b_rerepair_limit_and_only_flagged(tmp_path, monkeypatch):
    pred = _pred_dir(tmp_path)
    project = FakeProject(FakeResponses([]))
    monkeypatch.setattr(rr, "make_project_client", lambda endpoint: project)
    monkeypatch.setattr(rr, "AOAIClient", lambda: None)
    out_dir = tmp_path / "out"
    rc = rr.main([str(pred), "--out", str(out_dir), "--endpoint", "https://x/api/projects/p", "--limit", "0"])
    assert rc == 0 and project.agents.created == []  # limit 0 = 一案都不修,只複製
    assert len(list(out_dir.glob("C00?.json"))) == 3


# ---------- O13 run_pipeline / run_case 串接 ----------
def test_o13_run_case_calls_repairer_after_gate_and_records_usage(tmp_path):
    cid = "C001"
    fake = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    seen: list[str] = []

    def repairer(out: PipelineOutput, bundle: Any) -> PipelineOutput:
        seen.append(out.claim_id)
        rec = RepairRecord(invoked=True, plan="repairable", rounds=1, flags_before=list(out.gate.flags), flags_after=[],
                           guard_reason="gate_flags", pre_repair_decision=out.adjudication.decision,
                           pre_repair_amount=out.adjudication.approved_amount,
                           usage=[{"input_tokens": 50, "output_tokens": 5, "cached_tokens": 10}])
        return out.model_copy(update={"repair": rec})

    rec = runner.run_case(CLAIMS / cid, "full", FakeDI(CLAIMS / cid), fake, None, INDEX, tmp_path, repairer=repairer)
    assert seen == [cid] and rec.status == "ok"
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert out.repair is not None and out.repair.guard_reason == "gate_flags"
    assert [c["name"] for c in rec.calls] == ["extraction", "decision", "repair"]
    assert rec.calls[2]["prompt_tokens"] == 50 and rec.calls[2]["cached_tokens"] == 10


def test_o13b_without_repairer_output_has_no_repair_and_calls_unchanged(tmp_path):
    cid = "C001"
    fake = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(CLAIMS / cid, "full", FakeDI(CLAIMS / cid), fake, None, INDEX, tmp_path)
    raw = json.loads((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert "repair" not in raw and [c["name"] for c in rec.calls] == ["extraction", "decision"]
