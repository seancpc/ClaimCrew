"""eval v2 指標函式的手算案例測試(以 PipelineOutput 為評測單位)。"""

from decimal import Decimal

from app.gate.policy_index import load_policy_index
from app.schemas.claim import (
    AdjudicationResult,
    ClaimDecision,
    GateFlag,
    GateResult,
    PipelineOutput,
    PolicyCitation,
)
from eval.run_eval import evaluate

INDEX = load_policy_index()
GOOD_CIT = PolicyCitation(policy_id="POL-A", clause_no="第七條",  # 故意用中文數字,驗正規化
                          quote="每日給付「住院日額保險金」新臺幣貳仟元", relevance="")
BAD_CIT = PolicyCitation(policy_id="POL-A", clause_no="第99條", quote="", relevance="")


def _out(cid, decision, amount, citations=(), final=None, flags=()):
    d = ClaimDecision(decision)
    f = ClaimDecision(final) if final else d
    adj = AdjudicationResult(claim_id=cid, decision=d, approved_amount=Decimal(amount),
                             citations=list(citations), reasoning="", confidence=0.9)
    gate = GateResult(claim_id=cid, original_decision=d, final_decision=f,
                      flags=[GateFlag(check=x, detail="") for x in flags], passed=not flags)
    return PipelineOutput(claim_id=cid, adjudication=adj, gate=gate)


TRUTHS = {
    "T1": {"claim_id": "T1", "decision": "approve", "approved_amount": "1000", "clause_basis": ["POL-A 第7條"]},
    "T2": {"claim_id": "T2", "decision": "deny", "approved_amount": "0", "clause_basis": ["POL-A 第22條"]},
    "T3": {"claim_id": "T3", "decision": "manual_review", "approved_amount": "0", "clause_basis": []},
    "T4": {"claim_id": "T4", "decision": "approve", "approved_amount": "500",
           "clause_basis": ["POL-A 第7條", "POL-A 第11條"]},
}


def test_perfect_predictions():
    preds = {
        "T1": _out("T1", "approve", "1000", [GOOD_CIT]),
        "T2": _out("T2", "deny", "0", [PolicyCitation(policy_id="POL-A", clause_no="第22條",
                                                        quote="三十日內因疾病住院診療者", relevance="")]),
        "T3": _out("T3", "manual_review", "0"),
        "T4": _out("T4", "approve", "500", [GOOD_CIT, PolicyCitation(
            policy_id="POL-A", clause_no="11", quote="住院醫療輔助保險金", relevance="")]),
    }
    r = evaluate(TRUTHS, preds, INDEX)
    assert r["accuracy"] == 1.0 and r["amount_exact_rate"] == 1.0
    assert r["citation_precision"] == 1.0 and r["citation_total"] == 4
    assert r["clause_recall"] == 1.0
    assert r["manual_precision"] == 1.0 and r["manual_recall"] == 1.0
    assert r["gate_downgraded"] == 0


def test_scores_gate_final_not_llm_decision():
    """Gate 改派後,計分用 final_decision;金額於非核付時視為 0。"""
    preds = {
        "T1": _out("T1", "approve", "1000", [GOOD_CIT], final="manual_review", flags=["low_confidence"]),
        "T2": _out("T2", "deny", "0"),
        "T3": _out("T3", "manual_review", "0"),
        "T4": _out("T4", "approve", "500", [GOOD_CIT]),
    }
    r = evaluate(TRUTHS, preds, INDEX)
    assert r["confusion"]["approve"]["manual_review"] == 1
    assert r["gate_downgraded"] == 1
    assert "T1" not in r["amount_errors_by_case"]  # 最終非 approve 不計金額
    assert r["manual_precision"] == 0.5  # 轉人工 2 件,真值需人工 1 件


def test_worst_cells_and_amount_error():
    preds = {
        "T1": _out("T1", "approve", "1300", [GOOD_CIT]),   # 分類對、金額 +300
        "T2": _out("T2", "approve", "999", [GOOD_CIT]),    # 應拒付被核付(最傷格)
        "T3": _out("T3", "manual_review", "0"),
        "T4": _out("T4", "deny", "0"),                     # 應核付被拒付
    }
    r = evaluate(TRUTHS, preds, INDEX)
    assert r["accuracy"] == 0.5
    assert r["deny_approved_as_approve"] == 1 and r["approve_denied_as_deny"] == 1
    assert r["amount_errors_by_case"] == {"T1": "300"} and r["amount_mae"] == "300"


def test_citation_metrics_separate_precision_and_recall():
    preds = {
        "T1": _out("T1", "approve", "1000", [GOOD_CIT, BAD_CIT]),  # 2 引用 1 假
        "T2": _out("T2", "deny", "0", []),                        # 真值有依據、預測無 → recall 0
        "T3": _out("T3", "manual_review", "0"),
        "T4": _out("T4", "approve", "500", [GOOD_CIT]),          # 真值 2 條只中 1 → recall 0.5
    }
    r = evaluate(TRUTHS, preds, INDEX)
    assert r["citation_total"] == 3 and r["citation_precision"] == round(2 / 3, 4)
    assert r["clause_recall"] == round((1 + 0 + 0.5) / 3, 4)  # T3 真值無依據不計


def test_missing_prediction_not_counted_as_error():
    preds = {"T1": _out("T1", "approve", "1000", [GOOD_CIT]),
             "T2": _out("T2", "deny", "0"), "T3": _out("T3", "manual_review", "0")}
    r = evaluate(TRUTHS, preds, INDEX)
    assert r["missing"] == ["T4"] and r["n_scored"] == 3 and r["accuracy"] == 1.0


# --- B1-9 Gate 前準確率(SPEC 2026-10-01-b;72 案首跑靠此發現門檻在誤打誤撞擋錯)---
def test_pre_gate_accuracy_scores_original_decision():
    preds = {
        "T1": _out("T1", "approve", "1000", [GOOD_CIT], final="manual_review", flags=["low_confidence_fields"]),
        "T2": _out("T2", "approve", "0", final="manual_review", flags=["x"]),  # 模型判錯(應 deny),Gate 擋下
        "T3": _out("T3", "manual_review", "0"),
        "T4": _out("T4", "approve", "500", [GOOD_CIT]),
    }
    r = evaluate(TRUTHS, preds, INDEX)
    assert r["accuracy"] == 0.5  # Gate 後:T3、T4 對
    assert r["pre_gate_accuracy"] == 0.75  # Gate 前:T1、T3、T4 對,T2 錯
    assert r["gate_downgraded"] == 2
