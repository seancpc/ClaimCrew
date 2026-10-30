"""SPEC-r/s:自既有輸出目錄(<C###>.json + <C###>.bundle.json)離線產生人工覆核卡片並送 Logic Apps。

預設為 **摘要信**(SPEC-s,一輪一封;Outlook.com 逐案寄送第 6 封即被封鎖):先寫 <pred>/_review_digest.html 再 POST。
用法(桌機):
  $env:REVIEW_WEBHOOK_URL = "<Logic Apps HTTP 觸發器 URL,含 sig=>"   # 秘密,只放環境變數
  python tools\\send_review.py check\\pred_full_v9 --dry-run-dir check\\review_html   # 不打網路,只寫摘要 HTML 預覽
  python tools\\send_review.py check\\pred_full_v9                                      # 真送 1 封摘要
  python tools\\send_review.py check\\pred_full_v9 C055 --per-case --limit 1            # 逐案卡片(SPEC-r 路徑)
預設只送 final_decision == manual_review;--include-auto-deny 另送 P2 自動拒付;
--audit-rate(預設 0.10)另抽樣自動核付案作放行稽核。無 bundle 的案跳過並列出。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.review.card import (  # noqa: E402
    AUDIT_RATE,
    DIGEST_MAX_BYTES,
    Poster,
    ReviewCard,
    ReviewPost,
    build_review_card,
    mask_url,
    post_card,
    post_digest,
    should_review,
    split_digest,
    write_digests,
)
from app.schemas.claim import ClaimBundle, ClaimDecision, PipelineOutput  # noqa: E402

ENV_URL = "REVIEW_WEBHOOK_URL"
_poster: Poster | None = None  # 測試注入點;None = urllib


def _load_cards(pred: Path, case_ids: list[str], include_auto_deny: bool,
                audit_rate: float = 0.0) -> tuple[list[ReviewCard], int]:
    files = sorted(f for f in pred.glob("C*.json") if not f.name.endswith(".bundle.json"))
    if case_ids:
        files = [f for f in files if f.stem in set(case_ids)]
    cards: list[ReviewCard] = []
    skipped = 0
    for f in files:
        out = PipelineOutput.model_validate_json(f.read_text(encoding="utf-8"))
        if not should_review(out, include_auto_deny=include_auto_deny, audit_rate=audit_rate, run_tag=pred.name):
            continue
        bf = pred / f"{out.claim_id}.bundle.json"
        if not bf.is_file():
            print(f"{out.claim_id} skipped 無 bundle")
            skipped += 1
            continue
        audit = out.gate.final_decision is ClaimDecision.APPROVE
        bundle = ClaimBundle.model_validate_json(bf.read_text(encoding="utf-8"))
        cards.append(build_review_card(out, bundle, audit=audit))
    return cards, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pred_dir")
    ap.add_argument("case_ids", nargs="*", help="只處理這些案號;空 = 全部")
    ap.add_argument("--dry-run-dir", help="不送出,把 HTML 寫到此目錄供預覽")
    ap.add_argument("--per-case", action="store_true", help="逐案一封(SPEC-r 路徑;預設為一封摘要)")
    ap.add_argument("--limit", type=int, default=0, help="逐案模式:最多送幾案(信箱保險絲)")
    ap.add_argument("--max-bytes", type=int, default=DIGEST_MAX_BYTES, help="摘要單封上限,超過自動分封")
    ap.add_argument("--include-auto-deny", action="store_true", help="P2 程式改派之 deny 也送")
    ap.add_argument("--audit-rate", type=float, default=AUDIT_RATE,
                    help="SPEC-ae 放行抽樣稽核:自動核付案抽樣比例(0 關閉)")
    args = ap.parse_args(argv)
    pred = Path(args.pred_dir)
    url = os.environ.get(ENV_URL, "")
    if not args.dry_run_dir and not url:
        print(f"缺環境變數 {ENV_URL}(或改用 --dry-run-dir 預覽)", file=sys.stderr)
        return 2
    cards, skipped = _load_cards(pred, args.case_ids, args.include_auto_deny, args.audit_rate)
    errors = sent = 0
    if args.per_case:
        for card in cards:
            if args.limit and sent >= args.limit:
                break
            if args.dry_run_dir:
                dry = Path(args.dry_run_dir)
                dry.mkdir(parents=True, exist_ok=True)
                (dry / f"{card.claim_id}.html").write_text(card.html, encoding="utf-8")
                print(f"{card.claim_id} dry-run {len(card.html.encode('utf-8'))} bytes")
                sent += 1
                continue
            r: ReviewPost = post_card(url, card, post=_poster)
            sent += r.status == "sent"
            errors += r.status == "error"
            print(f"{card.claim_id} {r.status} http={r.http_status} run={r.run_id} {r.error}")
    else:
        digests = split_digest(cards, pred.name, args.max_bytes) if cards else []
        paths = write_digests(digests, Path(args.dry_run_dir) if args.dry_run_dir else pred)
        for d, p in zip(digests, paths, strict=True):
            if args.dry_run_dir:
                print(f"摘要 dry-run {len(d.claim_ids)} 案 {d.size_bytes} bytes → {p}")
                sent += 1
                continue
            r = post_digest(url, d, post=_poster)
            sent += r.status == "sent"
            errors += r.status == "error"
            print(f"摘要 {d.subject}:{r.status} http={r.http_status} run={r.run_id} {r.error}(已落地 {p})")
        if not cards:
            print("無符合條件之案,未寄送")
    print(f"\n完成:送出/預覽 {sent}、無 bundle 跳過 {skipped}、錯誤 {errors};目標 {mask_url(url) or '(dry-run)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
