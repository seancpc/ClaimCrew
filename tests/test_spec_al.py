"""SPEC-al 第一部分:POL-C ratio 一致性(`calc_ratio_inconsistent`)。

第12條「依第八條至第十一條約定計算之金額,按百分之六十五給付」→ 非健保時 8~11 條(含附錄一手術行)全部 × 0.65。
2026-10-10 SPEC-ak 乾淨對照 C035:病房行 ratio 1、手術 / 雜費 0.65,放行 118,800(真值 107,250,+11,550);
G2 `nhi_ratio_review` 只在「無任何 0.65 行」時觸發,漏接。歷史 v10/v14 同形狀靠引文旗標幸運轉人工。
POL-B 次高手術 50%(1 與 0.5 混用)為合法,不檢。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, ClaimDecision
from tests.test_spec_w import _bundle

INDEX = load_policy_index()
FLAG = "calc_ratio_inconsistent"


def _line(clause: str, kind: str, actual: str, cap: str, ratio: str, **kw: Any) -> dict[str, Any]:
    d = {"item": "t", "clause_no": clause, "kind": kind, "quantity": 0, "unit_amount": "0", "actual_amount": actual,
         "deduction": "0", "cap": cap, "lump_amount": "0", "ratio": ratio}
    d.update(kw)
    return d


def _c035(room: str = "1", surg: str = "0.65", misc: str = "0.65") -> list[dict[str, Any]]:
    """線上 run ak_clean(afe6c76)C035 明細原樣;預設即放行錯形狀。"""
    return [_line("第8條", "capped_per_unit", "46200", "3000", room, quantity=11, unit_amount="3000"),
            _line("第9條", "capped_actual", "95000", "80000", surg),
            _line("第10條", "capped_actual", "52000", "60000", misc)]


_CITES = [{"policy_id": "POL-C", "clause_no": cn, "quote": "", "relevance": "t", "segment": "本文"}
          for cn in ("第7條", "第8條", "第9條", "第10條", "第12條")]


def _gate(cid: str, lines: list[dict[str, Any]], cites: list[dict[str, Any]] | None = None):
    b = _bundle(cid)
    d = AdjudicationDraft.model_validate({"claim_id": cid, "decision": "approve", "calculation": lines,
                                          "citations": cites if cites is not None else _CITES,
                                          "reasoning": "r", "confidence": 0.95})
    adj = finalize_adjudication(d, b)
    return adj, checks.run_gate(adj, b, INDEX)


def _hits(g) -> list:
    return [f for f in g.flags if f.check == FLAG]


def test_al1_c035_online_shape_flagged_and_routed():
    adj, g = _gate("C035", _c035())
    assert adj.approved_amount == 118800  # 前提:線上放行金額
    assert len(_hits(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_al1_only_consistency_violated():
    """固定解法 33:同一明細只改病房 ratio 為 0.65 → 本檢核不觸發(其餘前提皆成立)。"""
    _, g = _gate("C035", _c035(room="0.65"))
    assert not _hits(g)


@pytest.mark.parametrize("ratios", [("0.65", "0.65", "0.65"), ("1", "1", "1")])
def test_al1_consistent_ratios_not_flagged(ratios):
    _, g = _gate("C035", _c035(*ratios))
    assert not _hits(g)


def test_al1_appendix_capped_line_counts_as_clause_9_group():
    """手術掛附錄一(SPEC-aj cap_group_clause)時也納入一致性比較;只有附錄一行比例不同,未歸組就漏接。"""
    lines = _c035(room="0.65", surg="1", misc="0.65")
    lines[1]["clause_no"] = "附錄一"
    _, g = _gate("C035", lines)
    assert len(_hits(g)) == 1


def test_al1_ambulance_line_included():
    lines = _c035(room="0.65") + [_line("第11條", "capped_actual", "1800", "2000", "1")]
    _, g = _gate("C035", lines)
    assert len(_hits(g)) == 1


def test_al2_pol_b_half_ratio_mix_is_legal():
    """POL-B 次高手術 50%:定額行 1 與 0.5 混用為合法,不檢(C051 膽囊第三級 + 疝氣第二級)。"""
    lines = [_line("附錄一", "lump_sum", "0", "0", "1", lump_amount="30000"),
             _line("第9條", "lump_sum", "0", "0", "0.5", lump_amount="15000")]
    cites = [{"policy_id": "POL-B", "clause_no": cn, "quote": "", "relevance": "t", "segment": "本文"}
             for cn in ("第7條", "第9條")]
    _, g = _gate("C051", lines, cites)
    assert not _hits(g)


def test_al2_non_approve_not_checked():
    b = _bundle("C035")
    d = AdjudicationDraft.model_validate({"claim_id": "C035", "decision": "manual_review", "calculation": _c035(),
                                          "citations": _CITES, "reasoning": "r", "confidence": 0.5})
    g = checks.run_gate(finalize_adjudication(d, b), b, INDEX)
    assert not _hits(g)


# ── 第二部分:報錯案輸出 manual_review(pipeline_error)───────────────
# SPEC-ak 重度劣化 9/13 案報錯(保單號碼 OCR 讀壞 7、收據金額格式讀壞 2):原本只記 _run.jsonl,不輸出、不進摘要信。
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from app.pipeline import runner  # noqa: E402
from app.review.card import DigestCollector, build_review_card  # noqa: E402
from app.schemas.claim import PipelineOutput  # noqa: E402
from tests.test_pipeline import FakeAOAI, FakeDI, _bundle_payload, _case, _decision_payload  # noqa: E402

ERR_FLAG = "pipeline_error"


def _run_err(tmp_path: Path, bundle: dict[str, Any], **kw: Any):
    cid = bundle["claim_id"]
    aoai = FakeAOAI(bundle, _decision_payload(cid, "POL-A", "第7條"), **kw)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, backoff_s=0)
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    return rec, out


def _policy_unroutable(cid: str = "C001") -> dict[str, Any]:
    b = _bundle_payload(cid)
    b["claim_application"]["policy_number"]["value"] = "POL G 2026-01"  # SPEC-ak 重度 C036 原樣
    return b


def test_al3_unroutable_policy_outputs_manual_with_pipeline_error(tmp_path: Path):
    rec, out = _run_err(tmp_path, _policy_unroutable())
    assert rec.status == "error"
    assert out.gate.final_decision is ClaimDecision.MANUAL_REVIEW
    assert out.adjudication.decision is ClaimDecision.MANUAL_REVIEW and out.adjudication.approved_amount == 0
    assert [f.check for f in out.gate.flags] == [ERR_FLAG]
    assert "無法由保單號碼歸戶" in out.gate.flags[0].detail


def test_al3_schema_error_detail_strips_raw_model_text(tmp_path: Path):
    """ModelOutputError 訊息帶「原文片段」(可能含病患姓名;此時無 bundle 可遮罩)→ detail 不得含原文。"""
    b = _bundle_payload("C001")
    b["medical_receipt"]["items"][0]["amount"] = "2:420"  # SPEC-ak 重度 C003 原樣
    name = b["diagnosis_certificate"]["patient_name"]["value"]
    rec, out = _run_err(tmp_path, b)
    detail = out.gate.flags[0].detail
    assert "原文片段" in rec.error and name in rec.error  # 前提:本地 log 仍保留完整訊息
    assert "ClaimBundle" in detail and "原文片段" not in detail and name not in detail
    assert name not in (tmp_path / "C001.json").read_text(encoding="utf-8")


def test_al3_transient_exhausted_also_routes_manual(tmp_path: Path):
    rec, out = _run_err(tmp_path, _bundle_payload("C001"), fail_times=99)
    assert rec.status == "error" and out.gate.flags[0].check == ERR_FLAG
    assert out.gate.flags[0].detail.startswith("TransientError")


def test_al4_error_case_reaches_digest_without_bundle(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_policy_unroutable(cid), _decision_payload(cid, "POL-A", "第7條"))
    collector = DigestCollector("t")
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, reviewer=collector)
    assert rec.review == "queued" and len(collector.cards) == 1
    card = collector.cards[0]
    assert card.claim_id == cid and card.facts == {} and ERR_FLAG in card.html


def test_al4_card_without_bundle_renders():
    out = PipelineOutput.model_validate_json(json.dumps({
        "claim_id": "C001",
        "adjudication": {"claim_id": "C001", "decision": "manual_review", "calculation": [], "citations": [],
                         "reasoning": "r", "confidence": 0.0, "approved_amount": "0"},
        "gate": {"claim_id": "C001", "original_decision": "manual_review", "final_decision": "manual_review",
                 "flags": [{"check": ERR_FLAG, "detail": "x"}], "passed": False}}))
    card = build_review_card(out, None)
    assert card.facts == {} and "C001" in card.subject


def test_al4_ok_case_output_unchanged_by_error_path(tmp_path: Path):
    """正常案輸出不帶 pipeline_error(位元組不變由既有落盤測試守住)。"""
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert rec.status == "ok" and ERR_FLAG not in {f.check for f in out.gate.flags}


def test_al5_regate_keeps_pipeline_error_output(tmp_path: Path):
    """保單歸戶失敗發生在抽取之後(bundle.json 已寫出);regate 不得重跑 Gate 而抹掉 pipeline_error。"""
    from tools.regate import regate

    rec, _ = _run_err(tmp_path, _policy_unroutable())
    assert (tmp_path / "C001.bundle.json").is_file()  # 前提:bundle 存在才會走重跑分支
    out = regate(tmp_path)["C001"]
    assert [f.check for f in out.gate.flags] == [ERR_FLAG]
    assert out.gate.final_decision is ClaimDecision.MANUAL_REVIEW
