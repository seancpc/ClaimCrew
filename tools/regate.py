"""離線重跑 Gate(SPEC 2026-10-01-b C3):以既有 <pred>/<C###>.json + <C###>.bundle.json 重算 GateResult,不呼叫 LLM。

用途:Gate 規則/門檻變更後,不重跑 72 案就能看旗標與 eval 分數的變化(Gate 為純函式)。
用法:
    python3 tools/regate.py check/pred_full                      # 只印旗標統計與 eval 摘要
    python3 tools/regate.py check/pred_full --out check/pred_full_regated   # 另存重算後的 PipelineOutput
    python3 tools/regate.py check/pred_full --field-threshold 0.70          # 門檻格搜用(只影響本次,不改程式常數)
缺 bundle 的案(缺件案)沿用原 GateResult。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate import checks  # noqa: E402
from app.gate.policy_index import load_policy_index  # noqa: E402
from app.pipeline.runner import finalize_adjudication  # noqa: E402
from app.schemas.claim import AdjudicationDraft, ClaimBundle, PipelineOutput  # noqa: E402
from eval.run_eval import evaluate, load_truths  # noqa: E402


def regate(pred_dir: Path, field_threshold: float | None = None,
           confidence_threshold: float | None = None, merge_split_lines: bool = True) -> dict[str, PipelineOutput]:
    """回傳 {claim_id: 重算後 PipelineOutput};有 bundle 的案重跑 run_gate,其餘原樣。"""
    index = load_policy_index()
    if field_threshold is not None:
        checks.EXTRACTION_FIELD_CONFIDENCE_THRESHOLD = field_threshold
    if confidence_threshold is not None:
        checks.CONFIDENCE_THRESHOLD = confidence_threshold
    outs: dict[str, PipelineOutput] = {}
    for f in sorted(pred_dir.glob("C*.json")):
        if f.name.endswith(".bundle.json"):
            continue
        out = PipelineOutput.model_validate_json(f.read_text(encoding="utf-8"))
        bundle_file = pred_dir / f"{out.claim_id}.bundle.json"
        # SPEC-al②:報錯案(pipeline_error)無判定可重算,原樣保留以免重跑 Gate 抹掉錯誤原因
        if bundle_file.is_file() and not any(f.check == "pipeline_error" for f in out.gate.flags):
            bundle = ClaimBundle.model_validate_json(bundle_file.read_text(encoding="utf-8"))
            # SPEC-p P4:存檔的 adjudication 重新 finalize(套用 P1 合併)再重跑 Gate,使歷史輸出可量測新規則
            # 程式算出的欄位(核付金額、SPEC-x 可確定金額)不屬草稿,轉回草稿前一併排除
            draft = AdjudicationDraft.model_validate(
                out.adjudication.model_dump(exclude={"approved_amount", "undisputed_amount"}))
            adj = finalize_adjudication(draft, bundle, merge_split_lines=merge_split_lines)
            out = PipelineOutput(claim_id=out.claim_id, adjudication=adj,
                                 gate=checks.run_gate(adj, bundle, index), repair=out.repair)
        outs[out.claim_id] = out
    return outs


def flag_summary(outs: dict[str, PipelineOutput]) -> dict[str, Any]:
    by_case: Counter[str] = Counter()
    by_flag: Counter[str] = Counter()
    for o in outs.values():
        for k in {fl.check for fl in o.gate.flags}:
            by_case[k] += 1
        for fl in o.gate.flags:
            by_flag[fl.check] += 1
    return {"cases_with_flag": dict(by_case), "flag_instances": dict(by_flag),
            "downgraded": sum(o.gate.final_decision != o.gate.original_decision for o in outs.values())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pred_dir")
    ap.add_argument("--out", help="另存重算後 PipelineOutput 的目錄(不複製 bundle)")
    ap.add_argument("--field-threshold", type=float)
    ap.add_argument("--confidence-threshold", type=float)
    ap.add_argument("--no-merge-split-lines", action="store_true",
                    help="SPEC-q 後 P1 合併預設開;此旗標關閉以量測對照(v7 C069 洞已由收據覆蓋檢核接住)")
    args = ap.parse_args(argv)
    outs = regate(Path(args.pred_dir), args.field_threshold, args.confidence_threshold,
                  merge_split_lines=not args.no_merge_split_lines)
    report: dict[str, Any] = {"flags": flag_summary(outs)}
    truths = load_truths()
    if outs:  # SPEC-y:舊輸出缺新增案(C073)時仍以現有案評分,缺件列於 eval.missing
        report["eval"] = evaluate(truths, outs, load_policy_index())
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        for cid, o in outs.items():
            (out_dir / f"{cid}.json").write_text(o.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
