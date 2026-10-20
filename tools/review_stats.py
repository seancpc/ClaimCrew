"""SPEC-am 覆核閉環統計(桌機:下載 runs/reviews-inbox 後執行)。

用法:python tools/review_stats.py --reviews check\\reviews --pred check\\<run>\\ --run <run_id> [--out report.json]
報表只含案號、決定與計數;覆核備註為自由文字(可能含姓名),不輸出(固定解法 21)。
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.review.card import should_review  # noqa: E402
from app.review.record import ReviewRecord, latest_by_case, load_reviews  # noqa: E402
from app.schemas.claim import ClaimDecision, PipelineOutput  # noqa: E402

BURST_GAP_S = 5  # pre-mortem 徵兆:連續送出間隔小於此值
BURST_WINDOW_S = 60  # 且整串落在此時間窗內
BURST_MIN = 3
SCANNER_UA = ("safelinks", "bot", "crawler", "spider", "scanner", "preview", "python-requests", "curl", "headless")


def _cards(pred_dir: Path) -> dict[str, PipelineOutput]:
    outs = {}
    for f in sorted(pred_dir.glob("C*.json")):
        if f.name.endswith(".bundle.json"):
            continue
        out = PipelineOutput.model_validate_json(f.read_text(encoding="utf-8"))
        if should_review(out):
            outs[out.claim_id] = out
    return outs


def _disagrees(r: ReviewRecord, truth: dict[str, Any]) -> bool:
    if r.decision.value != truth["decision"]:
        return True
    return (r.decision is ClaimDecision.APPROVE and r.amount is not None
            and r.amount != Decimal(str(truth["approved_amount"])))


def _suspicious(latest: dict[str, ReviewRecord]) -> list[str]:
    hits = {cid for cid, r in latest.items() if any(k in r.user_agent.lower() for k in SCANNER_UA)}
    seq = sorted(latest.values(), key=lambda r: r.submitted_at)
    i = 0
    while i < len(seq):
        j = i
        while (j + 1 < len(seq) and (seq[j + 1].submitted_at - seq[j].submitted_at).total_seconds() < BURST_GAP_S
               and (seq[j + 1].submitted_at - seq[i].submitted_at).total_seconds() <= BURST_WINDOW_S):
            j += 1
        if j - i + 1 >= BURST_MIN:
            hits |= {r.claim_id for r in seq[i:j + 1]}
        i = j + 1
    return sorted(hits)


def compute(reviews_dir: Path, pred_dir: Path, run_id: str, truths: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records, invalid = load_reviews(reviews_dir)
    latest, superseded = latest_by_case(records, run_id)
    cards = _cards(pred_dir)
    reviewed = {cid: r for cid, r in latest.items() if cid in cards}
    return {
        "run_id": run_id,
        "cards": len(cards),
        "reviewed": len(reviewed),
        "coverage": f"{len(reviewed) / len(cards):.3f}" if cards else "0.000",
        "overrides": sorted(cid for cid, r in reviewed.items() if r.decision is not cards[cid].gate.original_decision),
        "disagree_with_truth": sorted(cid for cid, r in reviewed.items()
                                      if cid in truths and _disagrees(r, truths[cid])),
        "superseded": superseded,
        "invalid": invalid,
        "suspicious": _suspicious(latest),
        "not_in_cards": sorted(set(latest) - set(cards)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SPEC-am 覆核閉環統計")
    ap.add_argument("--reviews", required=True, help="下載的 runs/reviews-inbox 目錄")
    ap.add_argument("--pred", required=True, help="該輪管線輸出目錄")
    ap.add_argument("--run", required=True, help="run_id(摘要主旨標記)")
    ap.add_argument("--out", default=None, help="報表 JSON 輸出路徑")
    args = ap.parse_args(argv)
    from eval.run_eval import load_truths

    report = compute(Path(args.reviews), Path(args.pred), args.run, load_truths())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(f"覆核 {report['reviewed']}/{report['cards']} 張卡片(涵蓋 {report['coverage']});"
          f"推翻原判 {len(report['overrides'])}、與真值不一致 {len(report['disagree_with_truth'])}、"
          f"可疑 {len(report['suspicious'])}、無效 {report['invalid']}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
