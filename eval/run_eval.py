"""eval v2:對凍結案件集評「系統最終輸出」(Gate 後),含 SPEC §6 四類指標。

用法:
    python3 -m eval.run_eval <predictions_dir>   # 每案一個 <claim_id>.json(PipelineOutput)
    python3 -m eval.run_eval --selfcheck         # 以真值反推 PipelineOutput,驗指標管線=滿分

指標(SPEC §6):
1. 判定準確率:三分類 accuracy + 混淆矩陣(以 gate.final_decision 計分,即系統真正輸出);
   兩個最傷格單獨拉出(deny→approve、approve→deny)
2. 核付金額:僅對「真值與最終判定皆 approve」的案件計 MAE 與全對率;最終判定非 approve 時金額視為 0
3. 引用正確率:citation_precision = 反查條款庫成立的引用 / 全部引用(條號正規化+引文子字串);
   clause_recall = 真值 clause_basis 被預測引用覆蓋的比例(對真值依據非空之案件取宏平均)
4. 分流品質:manual_precision = 轉人工案中真值亦為需人工的比例(轉得有沒有道理);
   manual_recall = 真值需人工案被轉人工的比例;gate_downgraded = Gate 改派件數
缺預測的案件記為 missing,不混入錯誤。
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.gate.checks import validate_citation
from app.gate.policy_index import Clause, load_policy_index, normalize_clause_no, normalize_policy_id
from app.schemas.claim import (
    AdjudicationResult,
    CalcKind,
    CalcLine,
    ClaimDecision,
    GateResult,
    PipelineOutput,
    PolicyCitation,
    SafetyRecord,
)

CLAIMS_DIR = Path(__file__).resolve().parents[1] / "data" / "claims"
DECISIONS = [d.value for d in ClaimDecision]


def load_truths(claims_dir: Path = CLAIMS_DIR) -> dict[str, dict[str, Any]]:
    truths = {}
    for case_dir in sorted(claims_dir.iterdir()):
        truth_file = case_dir / "truth.json"
        if truth_file.is_file():
            t = json.loads(truth_file.read_text(encoding="utf-8"))
            truths[t["claim_id"]] = t
    return truths


def load_predictions(pred_dir: Path, claim_ids: list[str]) -> dict[str, PipelineOutput]:
    preds = {}
    for cid in claim_ids:
        f = pred_dir / f"{cid}.json"
        if f.is_file():
            out = PipelineOutput.model_validate_json(f.read_text(encoding="utf-8"))
            side = pred_dir / f"{cid}.safety.json"  # SPEC-v:tools/resafety 離線補跑的 sidecar,有則併入(不碰判定)
            if out.safety is None and side.is_file():
                rec = SafetyRecord.model_validate_json(side.read_text(encoding="utf-8"))
                out = out.model_copy(update={"safety": rec})
            preds[cid] = out
    return preds


def _basis_key(policy_id: str, clause_no: str) -> str:
    return f"{normalize_policy_id(policy_id)} {normalize_clause_no(clause_no)}"


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def evaluate(
    truths: dict[str, dict[str, Any]],
    preds: dict[str, PipelineOutput],
    index: dict[str, dict[str, Clause]] | None = None,
) -> dict[str, Any]:
    index = index if index is not None else load_policy_index()
    confusion = {t: {p: 0 for p in DECISIONS} for t in DECISIONS}
    missing = [cid for cid in truths if cid not in preds]
    amount_errors: dict[str, str] = {}
    correct = pre_gate_correct = 0
    cit_total = cit_valid = 0
    recalls: list[float] = []
    pred_manual = pred_manual_hit = truth_manual = 0
    downgraded = 0
    und_scored = und_hit = 0  # SPEC-x 可確定金額(顧問欄位;不影響 accuracy)

    for cid, truth in truths.items():
        if cid in missing:
            continue
        out = preds[cid]
        final = out.gate.final_decision.value
        confusion[truth["decision"]][final] += 1
        correct += truth["decision"] == final
        pre_gate_correct += truth["decision"] == out.gate.original_decision.value  # 模型原判(Gate 前)
        if out.gate.final_decision != out.adjudication.decision:
            downgraded += 1
        # 金額:最終判定非 approve 視為 0
        if truth["decision"] == "approve" and final == "approve":
            amount_errors[cid] = str(out.adjudication.approved_amount - Decimal(truth["approved_amount"]))
        # 引用正確率
        cits = out.adjudication.citations
        cit_total += len(cits)
        cit_valid += sum(1 for c in cits if validate_citation(c, index) is None)
        truth_basis = set(truth.get("clause_basis", []))
        if truth_basis:
            pred_basis = {_basis_key(c.policy_id, c.clause_no) for c in cits}
            recalls.append(len(truth_basis & pred_basis) / len(truth_basis))
        # 分流品質
        if final == "manual_review":
            pred_manual += 1
            pred_manual_hit += truth["decision"] == "manual_review"
        truth_manual += truth["decision"] == "manual_review"
        if "undisputed_amount" in truth and final == "manual_review":
            und_scored += 1
            und_hit += out.adjudication.undisputed_amount == Decimal(truth["undisputed_amount"])

    scored = len(truths) - len(missing)
    abs_errs = [abs(Decimal(e)) for e in amount_errors.values()]
    report: dict[str, Any] = {
        "n_cases": len(truths),
        "n_scored": scored,
        "missing": missing,
        "accuracy": _ratio(correct, scored),
        "pre_gate_accuracy": _ratio(pre_gate_correct, scored),
        "confusion": confusion,
        "deny_approved_as_approve": confusion["deny"]["approve"],
        "approve_denied_as_deny": confusion["approve"]["deny"],
        "amount_mae": str(sum(abs_errs) / len(abs_errs)) if abs_errs else None,
        "amount_exact_rate": _ratio(sum(1 for e in abs_errs if e == 0), len(abs_errs)),
        "amount_errors_by_case": amount_errors,
        "citation_precision": _ratio(cit_valid, cit_total),
        "citation_total": cit_total,
        "clause_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "manual_precision": _ratio(pred_manual_hit, pred_manual),
        "manual_recall": _ratio(pred_manual_hit, truth_manual),
        "gate_downgraded": downgraded,
        "undisputed_exact_rate": _ratio(und_hit, und_scored),
        "undisputed_scored": und_scored,
    }
    g = groundedness_summary(preds)
    if g is not None:
        report["groundedness"] = g
    return report


def _quantile(sorted_vals: list[Decimal], q: Decimal) -> Decimal:
    """最近秩法(nearest-rank):第 ceil(q·n) 個;n=1 回唯一值。"""
    import math
    idx = max(1, math.ceil(q * len(sorted_vals))) - 1
    return sorted_vals[idx]


_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def reasoning_language(text: str) -> str:
    """理由主要語言:拉丁字母數 > 2 × 中日韓字元數 → en,否則 zh(條號/保單代號夾雜英文字母不算英文理由;
    SPEC-v:v9 A 的 269 無據句中 110 句為英文)。"""
    return "en" if len(_LATIN.findall(text)) > 2 * len(_CJK.findall(text)) else "zh"


def groundedness_summary(preds: dict[str, PipelineOutput]) -> dict[str, Any] | None:
    """SPEC-v 影子欄位量測:依 Gate 後 final_decision 分群的 ungrounded 比例分布;無任何 safety 結果時回 None。"""
    vals: dict[str, list[Decimal]] = {d: [] for d in DECISIONS}
    flagged: dict[str, int] = {d: 0 for d in DECISIONS}
    by_lang: dict[str, dict[str, list[Decimal]]] = {"zh": {}, "en": {}}
    n_with = n_missing = 0
    for out in preds.values():
        g = out.safety.groundedness if out.safety is not None else None
        if g is None:
            n_missing += 1
            continue
        n_with += 1
        fd = out.gate.final_decision.value
        vals[fd].append(g.percentage)
        flagged[fd] += g.ungrounded
        by_lang[reasoning_language(out.adjudication.reasoning)].setdefault(fd, []).append(g.percentage)
    if n_with == 0:
        return None
    by: dict[str, Any] = {}
    for d in DECISIONS:
        v = sorted(vals[d])
        if v:
            mid = (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2
            by[d] = {"n": len(v), "median": str(mid.normalize()), "p90": str(_quantile(v, Decimal("0.9")).normalize()),
                     "ungrounded": flagged[d]}
    def _med(v: list[Decimal]) -> str:
        v = sorted(v)
        return str(((v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2).normalize())
    lang = {lg: {d: {"n": len(v), "median": _med(v)} for d, v in groups.items() if v} for lg, groups in by_lang.items()}
    return {"n_with_result": n_with, "n_missing": n_missing, "by_final_decision": by, "by_language": lang}


def selfcheck_predictions(
    truths: dict[str, dict[str, Any]], index: dict[str, dict[str, Clause]]
) -> dict[str, PipelineOutput]:
    """以真值反推「完美管線輸出」:引文取條款原文前 20 字,Gate 無旗標放行。"""
    preds = {}
    for cid, t in truths.items():
        cits = []
        for basis in t.get("clause_basis", []):
            policy_id, clause_no = basis.split(" ", 1)
            clause = index[policy_id][clause_no]
            cits.append(PolicyCitation(policy_id=policy_id, clause_no=clause_no,
                                       quote=clause.text.strip()[:20], relevance="selfcheck"))
        decision = ClaimDecision(t["decision"])
        amount = Decimal(t["approved_amount"])
        # 完美管線輸出亦須帶計算明細(B1):以單行 lump_sum 表達真值金額
        zero = Decimal(0)
        calc = [CalcLine(item="selfcheck", clause_no="-", kind=CalcKind.LUMP_SUM, quantity=0, unit_amount=zero,
                         actual_amount=zero, deduction=zero, cap=zero, lump_amount=amount, ratio=Decimal(1))
                ] if amount else []
        adj = AdjudicationResult(claim_id=cid, decision=decision, approved_amount=amount, calculation=calc,
                                 citations=cits, reasoning="selfcheck", confidence=1.0,
                                 disputed_items=t.get("disputed_items", []),
                                 undisputed_amount=Decimal(t.get("undisputed_amount", "0")))
        gate = GateResult(claim_id=cid, original_decision=decision, final_decision=decision,
                          flags=[], passed=True)
        preds[cid] = PipelineOutput(claim_id=cid, adjudication=adj, gate=gate)
    return preds


def main(argv: list[str]) -> int:
    truths = load_truths()
    index = load_policy_index()
    if argv and argv[0] == "--selfcheck":
        preds = selfcheck_predictions(truths, index)
    elif argv:
        preds = load_predictions(Path(argv[0]), list(truths))
    else:
        print(__doc__)
        return 2
    report = evaluate(truths, preds, index)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if argv[0] == "--selfcheck":
        perfect = all(report[k] in (1.0, None) for k in
                      ("accuracy", "amount_exact_rate", "citation_precision", "clause_recall",
                       "manual_precision", "manual_recall", "undisputed_exact_rate")) and not report["missing"]
        print("SELFCHECK:", "PASS" if perfect else "FAIL")
        return 0 if perfect else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
