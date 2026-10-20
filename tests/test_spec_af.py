"""SPEC-af:合併限額少付(C059:出院後第 15 日再住院,第13條不視為同一次,
模型把兩次雜費 80,000 合成一行套 60,000)。
POL-C 第10條「每次住院」限額:明細行數 < 同一次住院組數,且 ratio 前合計超過「行數 × 限額」
→ 一個限額吃掉多次住院 → 人工。"""
from __future__ import annotations

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, ClaimDecision
from tests.test_pipeline import _decision_payload
from tests.test_spec_p import _capped, _capped_per_unit
from tests.test_spec_w import _bundle

INDEX = load_policy_index()
FLAG = "calc_stay_cap_merged"


def _run(lines, cid: str = "C059", policy: str = "POL-C", decision: str = "approve"):
    b = _bundle(cid)
    d = _decision_payload(cid, policy, "第10條")["adjudication"]
    d.update(calculation=lines, decision=decision)
    adj = finalize_adjudication(AdjudicationDraft.model_validate(d), b)  # 依真實 runner:有 bundle 才知住院組數
    return checks.run_gate(adj, b, INDEX)


def _hits(g) -> list:
    return [f for f in g.flags if f.check == FLAG]


def _misc(actual: int) -> dict:
    return _capped("第10條", actual, 60000).model_dump(mode="json")


ROOM = _capped_per_unit("第8條", 9, 3000, 22000)


def test_af1_merged_misc_line_flagged_manual():
    g = _run([ROOM, _misc(80000)])
    assert len(_hits(g)) == 1 and g.final_decision is ClaimDecision.MANUAL_REVIEW


def test_af2_split_per_stay_not_flagged():
    assert _hits(_run([ROOM, _misc(50000), _misc(30000)])) == []


def test_af2_single_line_within_one_cap_not_flagged():
    assert _hits(_run([ROOM, _misc(55000)])) == []


def test_af3_true_single_stay_group_not_checked():
    # C055:第 10 日再住院 → 第13條合併為同一次,一行套一個限額合法
    assert _hits(_run([ROOM, _misc(80000)], cid="C055")) == []


def test_af4_non_approve_not_checked():
    assert _hits(_run([ROOM, _misc(80000)], decision="manual_review")) == []


def test_af4_deduction_counted_before_cap():
    line = _capped("第10條", 80000, 60000, deduction=25000).model_dump(mode="json")
    assert _hits(_run([ROOM, line])) == []
