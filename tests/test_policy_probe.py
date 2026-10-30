"""SPEC 2026-10-02-m:tools/policy_probe.py——以既有 bundle 單獨打 Policy 呼叫,量 clause recall 與引用有效率。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.gate.policy_index import load_policy_index
from app.pipeline import aoai_client, intake
from app.pipeline.context import build_policy_context
from app.schemas.claim import ClaimBundle, PolicyCitation, PolicyResult
from tools import policy_probe as pp

INDEX = load_policy_index()
CLAIMS = Path(__file__).resolve().parents[1] / "data" / "claims"
PROMPTS = Path(__file__).resolve().parents[1] / "app" / "agents" / "prompts"


def _bundle(cid: str = "C001") -> ClaimBundle:
    # 以合成案 claim.json 建最小 bundle(與 tests/test_pipeline 的 _bundle_payload 同源欄位)
    from tests.test_pipeline import _bundle_payload
    return ClaimBundle.model_validate(_bundle_payload(cid))


def _intake(cid: str = "C001"):
    status, missing = intake.check_documents(CLAIMS / cid)
    return intake.intake_result(cid, status, missing, "POL-A", INDEX)


class FakeAOAI:
    def __init__(self, result: PolicyResult):
        self.result, self.calls = result, []

    def structured_call(self, **kw: Any) -> aoai_client.StructuredResponse:
        self.calls.append(kw)
        return aoai_client.StructuredResponse(content=self.result.model_dump_json(), model="m-policy",
                                              prompt_tokens=100, completion_tokens=10, cached_tokens=40)


def test_m1_messages_contain_policy_prompt_and_clauses_but_not_adjudication():
    clauses = build_policy_context("POL-A", "full", INDEX)
    system, user = pp.build_policy_messages(_bundle(), _intake(), clauses)
    policy_first = (PROMPTS / "policy.md").read_text(encoding="utf-8").splitlines()[0]
    adjudication_first = (PROMPTS / "adjudication.md").read_text(encoding="utf-8").splitlines()[0]
    assert policy_first in system and adjudication_first not in system
    assert all(f"【POL-A {c.clause_no}" in system for c in clauses)
    assert "policy_id" in system and "保單代號" in system  # 執行方式段:policy_id 填標頭保單代號
    payload = json.loads(user)
    assert set(payload) == {"claim_id", "intake", "claim_bundle"} and payload["claim_id"] == "C001"


def test_m2_score_recall_and_validity_hand_computed():
    truth = {"clause_basis": ["POL-A 第7條", "POL-A 第22條", "POL-A 第25條"]}
    cits = [
        PolicyCitation(policy_id="POL-A", clause_no="第7條", quote=INDEX["POL-A"]["第7條"].text[:20], relevance=""),
        PolicyCitation(policy_id="POL-A", clause_no="第22條", quote=INDEX["POL-A"]["第22條"].text[:20], relevance=""),
        PolicyCitation(policy_id="POL-A", clause_no="第99條", quote="不存在的條", relevance=""),  # 條號不存在 → 無效
    ]
    s = pp.score(PolicyResult(claim_id="C001", citations=cits, not_found_aspects=["等待期"]), truth, INDEX)
    assert round(s.recall, 4) == 0.6667 and round(s.valid, 4) == 0.6667 and s.n_citations == 3
    assert s.not_found_aspects == ["等待期"]
    # 分母必須是真值依據數(3),不是引用數(4):多引一條有效但非真值的第8條 → recall 仍 2/3,valid 升為 3/4
    extra = cits + [PolicyCitation(policy_id="POL-A", clause_no="第8條", quote=INDEX["POL-A"]["第8條"].text[:20],
                                   relevance="")]
    s4 = pp.score(PolicyResult(claim_id="C001", citations=extra, not_found_aspects=[]), truth, INDEX)
    assert round(s4.recall, 4) == 0.6667 and s4.valid == 0.75 and s4.n_citations == 4
    empty = pp.score(PolicyResult(claim_id="C001", citations=[], not_found_aspects=[]), truth, INDEX)
    assert empty.recall == 0.0 and empty.valid == 0.0 and empty.n_citations == 0


def test_m2_score_normalises_clause_numbers_like_eval():
    truth = {"clause_basis": ["POL-A 第7條"]}
    cit = PolicyCitation(policy_id="POL-A", clause_no="第七條", quote=INDEX["POL-A"]["第7條"].text[:20], relevance="")
    s = pp.score(PolicyResult(claim_id="C001", citations=[cit], not_found_aspects=[]), truth, INDEX)
    assert s.recall == 1.0 and s.valid == 1.0


def test_m3_probe_case_calls_policy_schema_once_with_deployment(tmp_path: Path):
    b = _bundle()
    (tmp_path / "C001.bundle.json").write_text(b.model_dump_json(), encoding="utf-8")
    fake = FakeAOAI(PolicyResult(claim_id="C001", citations=[], not_found_aspects=[]))
    result, resp = pp.probe_case(tmp_path / "C001.bundle.json", fake, INDEX, deployment="gpt-4o")
    assert isinstance(result, PolicyResult) and resp.cached_tokens == 40
    assert len(fake.calls) == 1 and fake.calls[0]["schema_name"] == "PolicyResult"
    assert fake.calls[0]["deployment"] == "gpt-4o"
    assert "adjudication" not in fake.calls[0]["system"].lower().split("# 本次執行方式")[0][-200:]


def test_m4_cli_prints_per_case_and_macro_average(tmp_path: Path, capsys, monkeypatch):
    b = _bundle()
    (tmp_path / "C001.bundle.json").write_text(b.model_dump_json(), encoding="utf-8")
    (tmp_path / "C006.json").write_text("{}", encoding="utf-8")  # 缺件案:無 bundle → 跳過
    cit = PolicyCitation(policy_id="POL-A", clause_no="第7條", quote=INDEX["POL-A"]["第7條"].text[:20], relevance="")
    fake = FakeAOAI(PolicyResult(claim_id="C001", citations=[cit], not_found_aspects=["除外責任"]))
    monkeypatch.setattr(pp, "make_client", lambda: fake)
    out_file = tmp_path / "probe.jsonl"
    assert pp.main([str(tmp_path), "--limit", "5", "--deployment", "gpt-4o", "--out", str(out_file)]) == 0
    out = capsys.readouterr().out
    assert "C001" in out and "recall" in out and "跳過 1" in out and "prompt=100" in out
    rows = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["claim_id"] == "C001" and rows[0]["citations"][0]["clause_no"] == "第7條"
