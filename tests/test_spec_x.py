"""SPEC-x(中間方案):manual_review 案的「可確定金額」顧問欄位——無放行權。
依據:保險法施行細則第7條(已認定部分應先行給付);本系統只呈現給人工,不付錢(ADR-008 顧問模式同精神)。"""
from __future__ import annotations

import json
from decimal import Decimal
from html import escape as html_escape
from pathlib import Path
from typing import Any

from app.datagen.cases import all_cases
from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.review.card import REDACTED, build_review_card, render_html
from app.schemas.claim import (
    AdjudicationDraft,
    ClaimBundle,
    ClaimDecision,
    PipelineOutput,
    to_structured_output_schema,
)
from eval.run_eval import evaluate, load_truths, selfcheck_predictions
from tests.test_pipeline import _bundle_payload, _calc_line, _decision_payload

ROOT = Path(__file__).resolve().parents[1]
INDEX = load_policy_index()


def _capped(clause: str, actual: int, cap: int) -> dict[str, Any]:
    d = _calc_line(clause, 0, "0")
    d.update(kind="capped_actual", actual_amount=str(actual), cap=str(cap))
    return d


def _cpu(clause: str, qty: int, unit: int, actual: int) -> dict[str, Any]:
    d = _calc_line(clause, qty, str(unit))
    d.update(kind="capped_per_unit", actual_amount=str(actual))
    return d


C041_LINES = [_cpu("第8條", 4, 3000, 8000), _capped("第10條", 3600, 60000), _capped("第10條", 2200, 60000)]
C064_LINES = [_calc_line("第7條", 2, "2000"), _calc_line("第11條", 2, "1000")]


def _draft(cid: str, policy: str, clause: str, decision: str, **over: Any) -> AdjudicationDraft:
    d = _decision_payload(cid, policy, clause)["adjudication"]
    d.update(decision=decision, calculation=[] if decision != "approve" else d["calculation"], **over)
    return AdjudicationDraft.model_validate(d)


# ---------- X1 schema ----------
def test_x1_draft_fields_default_empty_and_result_amount_defaults_zero():
    d = _draft("C041", "POL-C", "第8條", "manual_review")
    assert d.undisputed_calculation == [] and d.disputed_items == []
    r = finalize_adjudication(d)
    assert r.undisputed_amount == Decimal(0)


def test_x1_structured_schema_is_closed_and_requires_new_fields():
    s = to_structured_output_schema(AdjudicationDraft)
    assert {"undisputed_calculation", "disputed_items"} <= set(s["required"])
    assert s["additionalProperties"] is False


# ---------- X2 程式加總 ----------
def test_x2_c041_shape_sums_to_13800():
    d = _draft("C041", "POL-C", "第8條", "manual_review", undisputed_calculation=C041_LINES,
               disputed_items=["處置費 4,000:性質不明,依第26條補件"])
    r = finalize_adjudication(d, ClaimBundle.model_validate(_bundle_payload("C041")))
    assert r.undisputed_amount == Decimal(13800) and r.approved_amount == Decimal(0)


def test_x2_c064_shape_sums_to_6000():
    d = _draft("C064", "POL-A", "第7條", "manual_review", undisputed_calculation=C064_LINES,
               disputed_items=["射頻消融:手術定性與等級"])
    assert finalize_adjudication(d).undisputed_amount == Decimal(6000)


# ---------- X3 非 manual 強制清空 ----------
def test_x3_approve_and_deny_clear_advisory_fields():
    for decision in ("approve", "deny"):
        d = _draft("C041", "POL-C", "第8條", decision, undisputed_calculation=C041_LINES, disputed_items=["<項目>"])
        r = finalize_adjudication(d)
        assert r.undisputed_calculation == [] and r.disputed_items == [] and r.undisputed_amount == Decimal(0)


# ---------- X4 無放行權 ----------
def test_x4_advisory_fields_do_not_change_gate_or_approved_amount():
    b = ClaimBundle.model_validate(_bundle_payload("C041"))
    plain = finalize_adjudication(_draft("C041", "POL-C", "第8條", "manual_review"), b)
    adv = finalize_adjudication(_draft("C041", "POL-C", "第8條", "manual_review", undisputed_calculation=C041_LINES,
                                       disputed_items=["<項目>"]), b)
    g1, g2 = checks.run_gate(plain, b, INDEX), checks.run_gate(adv, b, INDEX)
    assert g1 == g2 and g2.final_decision is ClaimDecision.MANUAL_REVIEW
    assert plain.approved_amount == adv.approved_amount == Decimal(0)


# ---------- X5 舊檔相容 ----------
def test_x5_old_output_without_new_fields_loads():
    out = selfcheck_predictions(load_truths(), INDEX)["C003"]
    raw = json.loads(out.model_dump_json())
    for k in ("undisputed_calculation", "disputed_items", "undisputed_amount"):
        raw["adjudication"].pop(k, None)
    loaded = PipelineOutput.model_validate(raw)
    assert loaded.adjudication.undisputed_amount == Decimal(0) and loaded.adjudication.disputed_items == []


# ---------- X6 真值 ----------
def test_x6_truth_files_match_generator_spec_for_all_cases():
    for case in all_cases():
        on_disk = json.loads((ROOT / "data" / "claims" / case["claim_id"] / "truth.json").read_text(encoding="utf-8"))
        meta = {"claim_id": case["claim_id"], "scenario": case["scenario"], "synthetic": True}
        assert on_disk == {**meta, **case["truth"]}, case["claim_id"]


def test_x6_only_c041_c064_carry_undisputed_amount():
    t = load_truths()
    have = {cid for cid, v in t.items() if "undisputed_amount" in v}
    assert have == {"C041", "C064"}
    assert t["C041"]["undisputed_amount"] == "13800" and t["C064"]["undisputed_amount"] == "6000"
    assert all(t[c]["decision"] == "manual_review" and t[c]["approved_amount"] == "0" for c in have)
    assert all(t[c]["disputed_items"] for c in have)


def test_x6_c064_derivation_corrected_with_legal_basis():
    d = load_truths()["C064"]["derivation"]
    assert "POL-A 無部分給付機制" not in d and "施行細則第7條" in d


# ---------- X7 eval ----------
def test_x7_eval_reports_undisputed_exact_rate_and_selfcheck_is_perfect():
    t = load_truths()
    rep = evaluate(t, selfcheck_predictions(t, INDEX), INDEX)
    assert rep["undisputed_exact_rate"] == 1.0 and rep["undisputed_scored"] == 2


def test_x7_wrong_undisputed_amount_lowers_rate_but_not_accuracy():
    t = load_truths()
    preds = selfcheck_predictions(t, INDEX)
    adj = preds["C041"].adjudication.model_copy(update={"undisputed_amount": Decimal(17800)})
    preds["C041"] = preds["C041"].model_copy(update={"adjudication": adj})
    rep = evaluate(t, preds, INDEX)
    assert rep["undisputed_exact_rate"] == 0.5 and rep["accuracy"] == 1.0


# ---------- X8 卡片 ----------
def test_x8_card_shows_advisory_amount_with_label_and_redacts_names():
    b = ClaimBundle.model_validate(_bundle_payload("C041"))
    name = b.diagnosis_certificate.patient_name.value
    adj = finalize_adjudication(_draft("C041", "POL-C", "第8條", "manual_review", undisputed_calculation=C041_LINES,
                                       disputed_items=[f"{name} 之處置費性質不明"]), b)
    out = PipelineOutput(claim_id="C041", adjudication=adj, gate=checks.run_gate(adj, b, INDEX))
    card = build_review_card(out, b)
    html = render_html(card)
    assert "可確定金額" in html and "13800" in html and "未經確定性金額反查" in html
    assert name not in html and html_escape(REDACTED) in html  # 卡片全欄位 html.escape


def test_x8_card_without_advisory_shows_no_amount_line():
    b = ClaimBundle.model_validate(_bundle_payload("C041"))
    adj = finalize_adjudication(_draft("C041", "POL-C", "第8條", "manual_review"), b)
    out = PipelineOutput(claim_id="C041", adjudication=adj, gate=checks.run_gate(adj, b, INDEX))
    assert "可確定金額" not in render_html(build_review_card(out, b))


# ---------- X9 prompt(撤回,2026-10-05 桌機第三輪:模型原判 6/6 approve、欄位 6/6 空)----------
def test_x9_withdrawn_prompt_has_no_advisory_rule():
    text = (ROOT / "app" / "agents" / "prompts" / "adjudication.md").read_text(encoding="utf-8")
    assert "<!-- SPEC-x -->" not in text and "undisputed_calculation" not in text


def test_x9_withdrawn_schema_tells_model_to_leave_fields_empty():
    props = to_structured_output_schema(AdjudicationDraft)["properties"]
    for k in ("undisputed_calculation", "disputed_items"):
        assert "一律輸出空列表" in props[k]["description"]
