"""SPEC-v:離線補跑 Content Safety(groundedness + 四類掃描),不呼叫 LLM。

用法:python3 -m tools.resafety <pred_dir> [C### ...] [--limit N] [--endpoint URL]
以既有 <pred>/<C###>.json + <C###>.bundle.json 計算 SafetyRecord,寫 <C###>.safety.json;
既有 sidecar 不覆蓋、無 bundle 的案跳過(固定解法 11)。桌機跑 10 組歷史輸出量測假設 2(中文誤報率)。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import load_policy_index  # noqa: E402
from app.pipeline.safety import SafetyClient, safety_check  # noqa: E402
from app.schemas.claim import ClaimBundle, PipelineOutput  # noqa: E402

CS_ENDPOINT = os.environ.get("CS_ENDPOINT", "https://cs-claimcrew.cognitiveservices.azure.com/")


def main(argv: list[str] | None = None, client: SafetyClient | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pred_dir")
    ap.add_argument("case_ids", nargs="*")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--endpoint", default=CS_ENDPOINT)
    ap.add_argument("--force", action="store_true", help="覆蓋既有 sidecar(變體量測用;先備份舊檔)")
    ap.add_argument("--reasoning-only", action="store_true", help="被檢文字只含 reasoning,不含 citations[].relevance")
    ap.add_argument("--repeat", type=int, default=1, help="E1 重複性:同輸入連跑 N 次(需搭配 --tag)")
    ap.add_argument("--tag", default="", help="寫 C###.safety.<tag><i>.json,不碰正式 sidecar")
    args = ap.parse_args(argv)
    pred = Path(args.pred_dir)
    index = load_policy_index()
    if client is None:
        from app.pipeline.auth import make_cs_client
        client = make_cs_client(args.endpoint)
    done = skipped = 0
    for f in sorted(pred.glob("C*.json")):
        if f.name.endswith((".bundle.json", ".safety.json")):
            continue
        cid = f.name[:-5]
        if args.case_ids and cid not in args.case_ids:
            continue
        side, bundle_file = pred / f"{cid}.safety.json", pred / f"{cid}.bundle.json"
        tagged = [pred / f"{cid}.safety.{args.tag}{i}.json" for i in range(1, args.repeat + 1)]
        targets = tagged if args.tag else [side]
        if (all(t.exists() for t in targets) and not args.force) or not bundle_file.is_file():
            skipped += 1
            continue
        if args.limit and done >= args.limit:
            break
        out = PipelineOutput.model_validate_json(f.read_text(encoding="utf-8"))
        bundle = ClaimBundle.model_validate_json(bundle_file.read_text(encoding="utf-8"))
        for target in targets:
            rec = safety_check(out, bundle, client, index, reasoning_only=args.reasoning_only)
            target.write_text(rec.model_dump_json(indent=2), encoding="utf-8")
            g = rec.groundedness
            print(f"{cid}{'' if target == side else ' ' + target.name} ungrounded={g.percentage if g else '—'} "
                  f"harm={rec.harm} err={rec.error or '-'}")
        done += 1
    print(f"done={done} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
