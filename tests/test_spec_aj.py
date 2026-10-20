"""SPEC-aj:程式填引文(方案 A)。模型只指 segment(本文 / 一…N / 末段 / 附錄表格等級列 / 附錄二編號),
quote 由程式自條款庫填入;segment 為空(歷史輸出)時 quote 原樣,使 18 輪重放不漂移。
18 輪 1,300 案次:63 案次只因 citation_quote_mismatch 改派、模型原判(判定+金額)全對,72 筆全為改寫(附錄一表格重排 25)。"""
from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.gate import checks
from app.gate.policy_index import clause_segments, load_policy_index, normalize_segment
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, ClaimDecision, PolicyCitation, to_structured_output_schema
from tests.test_spec_w import _bundle

INDEX = load_policy_index()
ALL = [(p, cn) for p in INDEX for cn in INDEX[p]]
FLAG = "citation_segment_not_found"


# ── 行為 1:clause_segments 全量 ─────────────────────────────
def test_aj1_every_clause_segments_are_substrings_and_cover_all_lines():
    assert len(ALL) == 104
    for p, cn in ALL:
        c = INDEX[p][cn]
        segs = clause_segments(c)
        assert segs, (p, cn)
        for v in segs.values():
            assert v and v in c.text, (p, cn, v)
        lines = [ln.strip() for ln in c.text.split("\n") if ln.strip()]
        covered = "\n".join(segs.values())
        for ln in lines:
            if not ln.startswith("|---") and not ln.startswith("| 等級"):
                assert ln in covered, (p, cn, ln)


def test_aj1_shape_labels():
    assert list(clause_segments(INDEX["POL-A"]["第22條"])) == ["本文"]
    assert list(clause_segments(INDEX["POL-C"]["第9條"])) == ["本文", "一", "二", "三", "末段"]
    c10 = clause_segments(INDEX["POL-C"]["第10條"])
    assert list(c10) == ["本文", "一", "二", "三", "四", "五", "六", "末段"]
    assert c10["四"] == "四、注射、輸血及血液費用。"
    assert "診斷證明書費" in c10["末段"]
    assert list(clause_segments(INDEX["POL-B"]["第22條"])) == ["本文", "一", "二", "三", "四"]
    a1 = clause_segments(INDEX["POL-A"]["附錄一"])
    assert list(a1) == ["第一級", "第二級", "第三級"]
    assert a1["第二級"].startswith("| 第二級 |") and "膽囊切除術" in a1["第二級"]
    assert list(clause_segments(INDEX["POL-A"]["附錄二"])) == ["1", "2", "3", "4", "5", "6"]
    assert clause_segments(INDEX["POL-A"]["附錄二"])["4"] == "4. 肝硬化"


def test_aj1_appendix_rows_do_not_leak_header():
    for p in ("POL-A", "POL-B", "POL-C"):
        for v in clause_segments(INDEX[p]["附錄一"]).values():
            assert "手術項目(示範列舉)" not in v and "|---" not in v


# ── 行為 2:normalize_segment ─────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("一", "一"), ("一、", "一"), ("第一項", "一"), ("第一款", "一"), (" 三 ", "三"), ("第三項第一款", "三"),
    ("第二級", "第二級"), ("第二級手術", "第二級"), ("二級", "第二級"), ("本文", "本文"), ("末段", "末段"),
    ("4", "4"), ("4.", "4"),
])
def test_aj2_normalize_variants(raw, want):
    assert normalize_segment(raw) == want


def test_aj2_unknown_label_passes_through_and_does_not_resolve():
    segs = clause_segments(INDEX["POL-A"]["附錄一"])
    assert normalize_segment("第四級") not in segs
    assert normalize_segment("全文") not in clause_segments(INDEX["POL-C"]["第9條"])


@given(st.sampled_from(ALL))
def test_aj2_property_every_label_normalizes_to_itself(pc):
    for k in clause_segments(INDEX[pc[0]][pc[1]]):
        assert normalize_segment(k) == k


# ── 行為 3:finalize 填 quote ─────────────────────────────
def _cit(policy: str, clause: str, segment: str = "", quote: str = "") -> dict[str, Any]:
    return {"policy_id": policy, "clause_no": clause, "quote": quote, "relevance": "t", "segment": segment}


def _draft(cid: str, decision: str, cites: list[dict[str, Any]]) -> AdjudicationDraft:
    return AdjudicationDraft.model_validate({"claim_id": cid, "decision": decision, "calculation": [],
                                             "citations": cites, "reasoning": "r", "confidence": 0.95})


def test_aj3_resolvable_segment_fills_quote_from_index():
    adj = finalize_adjudication(_draft("C033", "manual_review", [_cit("POL-C", "第9條", "第二項")]), _bundle("C033"))
    assert adj.citations[0].quote == "二、第二級手術:限額為新臺幣捌萬元之百分之五十。"


def test_aj3_appendix_row_filled_and_clause_number_normalized():
    adj = finalize_adjudication(_draft("C054", "manual_review", [_cit("POL-A", "附錄 1", "第二級手術")]),
                                _bundle("C054"))
    assert adj.citations[0].quote == clause_segments(INDEX["POL-A"]["附錄一"])["第二級"]


def test_aj3_empty_segment_keeps_quote_verbatim():
    q = "模型原本寫的引文"
    adj = finalize_adjudication(_draft("C033", "manual_review", [_cit("POL-C", "第9條", "", q)]), _bundle("C033"))
    assert adj.citations[0].quote == q


def test_aj3_unresolvable_segment_keeps_quote():
    adj = finalize_adjudication(_draft("C033", "manual_review", [_cit("POL-C", "第9條", "九", "")]), _bundle("C033"))
    assert adj.citations[0].quote == ""


def test_aj3_legacy_output_without_segment_field_loads():
    c = PolicyCitation.model_validate({"policy_id": "POL-A", "clause_no": "第7條", "quote": "x", "relevance": "t"})
    assert c.segment == ""


# ── 行為 4:Gate 段落反查 ─────────────────────────────
def _cflags(c: dict[str, Any]) -> list[str]:
    f = checks.validate_citation(PolicyCitation.model_validate(c), INDEX)
    return [f.check] if f else []


def test_aj4_unknown_segment_with_otherwise_valid_quote_flagged_and_routes():
    """只違反段落一個條件:quote 為該條真實原文,仍因 segment 不存在被旗標(固定解法 33)。"""
    real = INDEX["POL-C"]["第9條"].text[:20]
    assert _cflags(_cit("POL-C", "第9條", "九", real)) == [FLAG]
    assert checks.routes_to_manual([checks.GateFlag(check=FLAG, detail="d")])


def test_aj4_known_segment_with_program_filled_quote_no_flag():
    adj = finalize_adjudication(_draft("C033", "manual_review", [_cit("POL-C", "第9條", "二"),
                                                                 _cit("POL-A", "附錄一", "第二級"),
                                                                 _cit("POL-C", "第10條", "末段")]), _bundle("C033"))
    assert [checks.validate_citation(c, INDEX) for c in adj.citations] == [None, None, None]


def test_aj4_empty_segment_falls_back_to_quote_check():
    assert _cflags(_cit("POL-C", "第9條", "", "改寫過的引文")) == ["citation_quote_mismatch"]
    assert _cflags(_cit("POL-C", "第9條", "", INDEX["POL-C"]["第9條"].text[:20])) == []


def test_aj4_segment_of_wrong_clause_flagged():
    """末段只存在於 PEP 形狀;第22條(單段)無末段 → 旗標,不得退回寬鬆比對。"""
    assert _cflags(_cit("POL-A", "第22條", "末段", INDEX["POL-A"]["第22條"].text)) == [FLAG]


@given(st.sampled_from(ALL))
def test_aj4_property_every_segment_round_trips_through_gate(pc):
    p, cn = pc
    for k in clause_segments(INDEX[p][cn]):
        adj = finalize_adjudication(_draft("C033", "manual_review", [_cit(p, cn, k)]), _bundle("C033"))
        assert checks.validate_citation(adj.citations[0], INDEX) is None
        assert adj.citations[0].quote == clause_segments(INDEX[p][cn])[k]


def test_aj4_end_to_end_gate_routes_unknown_segment():
    b = _bundle("C033")
    adj = finalize_adjudication(_draft("C033", "deny", [_cit("POL-C", "第9條", "九")]), b)
    g = checks.run_gate(adj, b, INDEX)
    assert FLAG in {f.check for f in g.flags} and g.final_decision is ClaimDecision.MANUAL_REVIEW


# ── 行為 5:schema 與 prompt ─────────────────────────────
def test_aj5_structured_schema_requires_segment():
    s = to_structured_output_schema(AdjudicationDraft)
    pc = s["$defs"]["PolicyCitation"]
    assert "segment" in pc["properties"] and "segment" in pc["required"]


def test_aj5_prompt_defines_segment_labels():
    from app.pipeline.runner import PROMPTS_DIR
    t = (PROMPTS_DIR / "adjudication.md").read_text(encoding="utf-8")
    for kw in ("segment", "本文", "末段", "等級"):
        assert kw in t
    assert "quote" in t and '""' in t


def test_aj4_empty_quote_and_empty_segment_flagged():
    """新 prompt 令 quote 填空字串;模型若漏填 segment,空 quote 不得被視為相符(既有 quote_matches 對空字串放行)。"""
    assert _cflags(_cit("POL-C", "第9條", "", "")) == [FLAG]
    assert _cflags(_cit("POL-C", "第9條", "", "   ")) == [FLAG]


def test_aj4_gate_normalizes_segment_variants():
    """模型寫「第二項」「第二級手術」:finalize 已填原文,Gate 也須以正規化後標籤反查,不得誤攔。"""
    cites = [_cit("POL-C", "第9條", "第二項"), _cit("POL-A", "附錄一", "第二級手術")]
    adj = finalize_adjudication(_draft("C033", "manual_review", cites), _bundle("C033"))
    assert [checks.validate_citation(c, INDEX) for c in adj.citations] == [None, None]


# ── 行為 6(SPEC-aj 修訂,2026-10-10 線上子集 C069):POL-C 附錄一限額行與第9條同組 ─────────────
from decimal import Decimal  # noqa: E402

from app.gate.calc import cap_group_clause, normalize_calculation, total_amount  # noqa: E402
from app.schemas.claim import CalcLine  # noqa: E402

C069_TRUTH = Decimal("24050")


def _cl(clause: str, kind: str, actual: str = "0", cap: str = "0", ratio: str = "0.65", **kw: Any) -> CalcLine:
    d = {"item": "t", "clause_no": clause, "kind": kind, "quantity": 0, "unit_amount": "0", "actual_amount": actual,
         "deduction": "0", "cap": cap, "lump_amount": "0", "ratio": ratio}
    d.update(kw)
    return CalcLine.model_validate(d)


def _c069_lines() -> list[CalcLine]:
    """線上 run 20261010T0356-df15101 C069 原樣:手術費掛附錄一、麻醉費掛第9條,各套 20,000。"""
    return [_cl("第8條", "capped_per_unit", "9000", quantity=3, unit_amount="3000"),
            _cl("附錄一", "capped_actual", "18000", "20000"),
            _cl("第9條", "capped_actual", "4000", "20000"),
            _cl("第10條", "capped_actual", "8000", "60000")]


def test_aj6_cap_group_maps_appendix_capped_line_to_clause_9():
    assert cap_group_clause(_cl("附錄一", "capped_actual", "1", "1")) == "第9條"
    assert cap_group_clause(_cl("第 9 條", "capped_actual", "1", "1")) == "第9條"
    assert cap_group_clause(_cl("附錄 1", "capped_actual", "1", "1")) == "第9條"


def test_aj6_cap_group_leaves_lump_sum_appendix_and_other_clauses():
    """POL-A/B 附錄一為 lump_sum 定額(非限額),不得併入第9條;其他條號只做正規化。"""
    assert cap_group_clause(_cl("附錄一", "lump_sum", lump_amount="25000", ratio="1")) == "附錄一"
    assert cap_group_clause(_cl("第10條", "capped_actual", "1", "1")) == "第10條"


def test_aj6_merge_combines_appendix_and_clause_9_c069_amount_equals_truth():
    merged = normalize_calculation(_c069_lines())
    assert len(merged) == 3
    assert total_amount(merged) == C069_TRUTH
    assert total_amount(_c069_lines()) == Decimal("25350")  # 未合併 = 線上放行金額(反例)


def test_aj6_cap_sum_flags_split_across_appendix_and_clause_9():
    """關閉合併(regate --no-merge-split-lines 對照)時,F1 上限加總也須以同組接住。"""
    b = _bundle("C069")
    draft = AdjudicationDraft.model_validate({"claim_id": "C069", "decision": "approve",
                                              "calculation": [ln.model_dump() for ln in _c069_lines()],
                                              "citations": [], "reasoning": "r", "confidence": 0.95})
    adj = finalize_adjudication(draft, b, merge_split_lines=False)
    assert "calc_cap_sum_exceeded" in {f.check for f in checks.run_gate(adj, b, INDEX).flags}


C069_CITES = [("第7條", "本文"), ("第8條", "本文"), ("附錄一", "第一級"), ("第9條", "本文"), ("第10條", "本文"),
              ("第12條", "本文"), ("第14條", "本文"), ("第23條", "本文")]  # 線上 C069 原樣


def test_aj6_c069_online_output_end_to_end_released_amount_correct():
    """線上 C069 明細 + 引文原樣:舊程式 0 旗標放行 25,350;修正後不得再有放行錯。"""
    b = _bundle("C069")
    draft = AdjudicationDraft.model_validate({"claim_id": "C069", "decision": "approve",
                                              "calculation": [ln.model_dump() for ln in _c069_lines()],
                                              "citations": [_cit("POL-C", cn, sg) for cn, sg in C069_CITES],
                                              "reasoning": "r", "confidence": 0.95})
    adj = finalize_adjudication(draft, b)
    g = checks.run_gate(adj, b, INDEX)
    assert adj.approved_amount == C069_TRUTH or g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_aj6_polc_grade_cap_checks_appendix_capped_line():
    """W2:POL-C 限額須 = 捌萬 × 最高等級%;掛在附錄一的限額行也要檢(C069 闌尾第一級 → 20,000)。"""
    b = _bundle("C069")
    lines = [_cl("附錄一", "capped_actual", "18000", "40000", ratio="1")]
    adj = finalize_adjudication(AdjudicationDraft.model_validate(
        {"claim_id": "C069", "decision": "approve", "calculation": [ln.model_dump() for ln in lines],
         "citations": [], "reasoning": "r", "confidence": 0.95}), b)
    assert "calc_surgery_grade_value" in {f.check for f in checks.run_gate(adj, b, INDEX).flags}


@pytest.mark.parametrize("raw,want", [("第1級", "第一級"), ("第2級手術", "第二級"), ("3級", "第三級"),
                                      ("第5級", "第五級")])
def test_aj6_arabic_grade_labels_normalize(raw, want):
    """線上 C054 寫「第1級」「第2級」。"""
    assert normalize_segment(raw) == want


def test_aj6_arabic_list_numbers_unchanged():
    """附錄二編號清單 1…6 不得被轉為中文數字。"""
    assert normalize_segment("4") == "4" and normalize_segment("4.") == "4"
