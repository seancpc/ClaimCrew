"""SPEC-q:收據覆蓋檢核——POL-C approve 時,收據命中的給付類別(第8~11條)必須有對應明細行,
否則 receipt_category_uncovered 改派人工(v7 C069:藥費/檢驗費無第10條行,少付 5,200 卻放行)。"""
from __future__ import annotations

from typing import Any

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload, _decision_payload
from tests.test_spec_p import _adj, _capped, _capped_per_unit

INDEX = load_policy_index()
FLAG = "receipt_category_uncovered"


def _items(payload: dict[str, Any], items: list[tuple[str, str]]) -> dict[str, Any]:
    payload["medical_receipt"]["items"] = [{"item_name": n, "amount": a} for n, a in items]
    return payload


def _uncovered(g) -> list:
    return [f for f in g.flags if f.check == FLAG]


# ---------- Q1 類別表 ----------
def test_q1_category_table_covers_clauses_8_to_11_and_excludes_negative_list():
    assert set(checks.RECEIPT_CLAUSE_CATEGORIES) == {"第8條", "第9條", "第10條", "第11條"}
    hit = checks.receipt_clauses_hit(["病房費", "手術費(含麻醉)", "藥費", "醫療雜費", "救護車費",
                                      "診斷證明書費", "掛號費", "看護費"])
    assert hit == {"第8條": ["病房費"], "第9條": ["手術費(含麻醉)"], "第10條": ["藥費", "醫療雜費"],
                   "第11條": ["救護車費"]}
    negatives = ("診斷證明", "病歷", "掛號", "電話", "電視", "伙食", "看護")
    assert all(n not in kw for kws in checks.RECEIPT_CLAUSE_CATEGORIES.values() for kw in kws for n in negatives)
    assert checks.receipt_clauses_hit(["診斷證明書費", "掛號費"]) == {}


# ---------- Q2 檢核 ----------
def test_q2_flags_c069_shape_missing_clause_10_line_and_routes_to_manual():
    bundle = ClaimBundle.model_validate(_bundle_payload("C069"))  # 病房/手術/麻醉/藥費/檢驗費
    adj = _adj("C069", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000),
                                                      _capped("第9條", 18000, 20000).model_dump(mode="json"),
                                                      _capped("第9條", 4000, 20000).model_dump(mode="json")])
    g = checks.run_gate(adj, bundle, INDEX)
    flags = _uncovered(g)
    assert len(flags) == 1
    assert "第10條" in flags[0].detail and "藥費" in flags[0].detail and "檢驗費" in flags[0].detail
    assert "第8條" not in flags[0].detail and "第9條" not in flags[0].detail
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_q2_appendix_one_line_covers_clause_9_c033_shape():
    bundle = ClaimBundle.model_validate(_bundle_payload("C033"))  # 病房/手術費(含麻醉)/醫療雜費/救護車費
    adj = _adj("C033", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 10, 3000, 35000),
                                                      _capped("附錄一", 62000, 100000).model_dump(mode="json"),
                                                      _capped("第10條", 41500, 60000).model_dump(mode="json"),
                                                      _capped("第11條", 1800, 2000).model_dump(mode="json")])
    assert _uncovered(checks.run_gate(adj, bundle, INDEX)) == []


def test_q2_negative_list_only_items_need_no_coverage():
    payload = _items(_bundle_payload("C069"), [("病房費", "9000"), ("診斷證明書費", "200"), ("掛號費", "150")])
    bundle = ClaimBundle.model_validate(payload)
    adj = _adj("C069", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000)])
    assert _uncovered(checks.run_gate(adj, bundle, INDEX)) == []


def test_q2_not_applied_to_pol_a_or_non_approve():
    payload = _items(_bundle_payload("C004"), [("病房費", "9000"), ("藥費", "5000")])  # POL-A 同形
    bundle_a = ClaimBundle.model_validate(payload)
    assert bundle_a.claim_application.policy_number.value.startswith("POL-A")
    adj_a = _adj("C004", "POL-A", "第7條")
    assert _uncovered(checks.run_gate(adj_a, bundle_a, INDEX)) == []
    bundle_c = ClaimBundle.model_validate(_bundle_payload("C069"))
    deny = _adj("C069", "POL-C", "第8條", decision="deny", calculation=[])
    assert _uncovered(checks.run_gate(deny, bundle_c, INDEX)) == []


# ---------- Q4 與 P1 合併同開:v7 C069 形不再放行 ----------
def test_q4_merge_split_lines_no_longer_releases_c069_shape():
    bundle = ClaimBundle.model_validate(_bundle_payload("C069"))
    d = _decision_payload("C069", "POL-C", "第8條")["adjudication"]
    d["calculation"] = [_capped_per_unit("第8條", 3, 3000, 9000),
                        _capped("第9條", 18000, 20000, item="手術費").model_dump(mode="json"),
                        _capped("第9條", 4000, 20000, item="麻醉費").model_dump(mode="json")]
    adj = finalize_adjudication(AdjudicationDraft.model_validate(d), bundle, merge_split_lines=True)
    assert len(adj.calculation) == 2  # 第9條兩行已合併,cap-sum 旗標不再出現
    g = checks.run_gate(adj, bundle, INDEX)
    assert not any(f.check.startswith("calc_cap_sum") for f in g.flags)
    assert len(_uncovered(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_q4_merge_split_lines_is_default_on_after_coverage_check():
    bundle = ClaimBundle.model_validate(_bundle_payload("C069"))
    d = _decision_payload("C069", "POL-C", "第8條")["adjudication"]
    d["calculation"] = [_capped("第9條", 18000, 20000).model_dump(mode="json"),
                        _capped("第9條", 4000, 20000).model_dump(mode="json")]
    adj = finalize_adjudication(AdjudicationDraft.model_validate(d), bundle)
    assert len(adj.calculation) == 1 and adj.approved_amount == 20000
