"""SPEC-ai:拒付方向的「手術定義」檢核(C031)。
POL-B 第2條第三款是三份保單中唯一以「單純之診斷性處置」排除手術的定義;模型以第2條拒付時,
是否構成手術屬認定爭議(C031 瘜肉切除、C007 胃鏡止血皆真值人工)→ 改派人工。
18 輪(v2–v16、route_v1、cloud1/2)POL-B 第2條拒付 5 次真值全為人工(C031×3、C007×2),其中 2 次被自動拒付。"""
from __future__ import annotations

from typing import Any

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, ClaimDecision
from tests.test_spec_w import _bundle

INDEX = load_policy_index()
FLAG = "surgery_definition_deny"


def _cit(policy: str, clause: str, raw: str | None = None) -> dict[str, Any]:
    return {"policy_id": policy, "clause_no": raw or clause, "quote": INDEX[policy][clause].text[:20], "relevance": "t"}


def _run(cid: str, decision: str, cites: list[dict[str, Any]], bundle=None):
    b = bundle or _bundle(cid)
    d = {"claim_id": cid, "decision": decision, "calculation": [], "citations": cites,
         "reasoning": "依第2條,單純之診斷性處置不視為手術", "confidence": 0.95}
    adj = finalize_adjudication(AdjudicationDraft.model_validate(d), b)
    return checks.run_gate(adj, b, INDEX)


def _hits(g) -> list:
    return [f for f in g.flags if f.check == FLAG]


def test_ai1_pol_b_deny_citing_definition_routes_manual():
    """v16 C031 形狀:deny 引第2條 + 第15條,無其他旗標 → 原本自動拒付,現改派人工。"""
    g = _run("C031", "deny", [_cit("POL-B", "第2條"), _cit("POL-B", "第15條")])
    assert len(_hits(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_ai1_clause_number_spacing_normalized():
    g = _run("C031", "deny", [_cit("POL-B", "第2條", raw="第 2 條")])
    assert len(_hits(g)) == 1


def test_ai1_c007_shape_also_caught():
    """v3 C007:胃鏡止血(摘要無切除字樣)deny 引第2條 → 人工(不以摘要關鍵字觸發,以免漏 C007)。"""
    g = _run("C007", "deny", [_cit("POL-B", "第2條")])
    assert len(_hits(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_ai2_other_policies_definition_deny_not_flagged():
    """POL-A C005 / POL-C C044:第2條無診斷性處置排除,歷史第2條拒付皆真值 deny → 不檢。"""
    assert not _hits(_run("C005", "deny", [_cit("POL-A", "第2條")]))
    assert not _hits(_run("C044", "deny", [_cit("POL-C", "第2條")]))


def test_ai3_pol_b_deny_without_definition_not_flagged():
    assert not _hits(_run("C031", "deny", [_cit("POL-B", "第15條")]))


def test_ai4_non_deny_citing_definition_not_flagged():
    assert not _hits(_run("C031", "manual_review", [_cit("POL-B", "第2條")]))

