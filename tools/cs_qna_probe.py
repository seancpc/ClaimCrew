"""SPEC-v 第二段 E2:groundedness QnA 模式逐主張探針(不呼叫 LLM)。

用法:python tools/cs_qna_probe.py <pred_dir> C### [C### ...] [--repeat 3] [--out check/qna_probe.json]
自 reasoning 抽三類主張句(既往症 / 住院日數 / 等待期,每類最先出現的一句),配固定問句以 task=QnA 送出,
記錄每案 × 主張 × 重複次數的無據判定。驗收(事前寫死):C066 既往主張 3/3 無據、C024 既往主張 3/3 有據、
乾淨案日數主張 ≥ 8/9 有據 → 「逐主張可用」;否則維持否決。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import load_policy_index  # noqa: E402
from app.pipeline.safety import SafetyClient, build_inputs, segment_sentences  # noqa: E402
from app.schemas.claim import ClaimBundle, PipelineOutput  # noqa: E402

CS_ENDPOINT = os.environ.get("CS_ENDPOINT", "https://cs-claimcrew.cognitiveservices.azure.com/")
# 問句用中性措辭(prompt 規範:不含具體案件內容)
QUESTIONS = {
    "pre_existing": "診斷書是否記載被保險人有既往症、病史或投保前已診斷之疾病?",
    "days": "本次住院日數為幾日?",
    "waiting": "事件距保單生效日是否已逾等待期?",
}
_PATTERNS = {
    "pre_existing": re.compile(r"既往|投保前|病史|pre-existing", re.I),
    "days": re.compile(r"(?:\d+|[一二三四五六七八九十]+)\s*(?:日|天)"),
    "waiting": re.compile(r"等待期|生效日|waiting period", re.I),
}


def extract_claims(reasoning: str) -> list[tuple[str, str]]:
    """每類最先出現的一句(依類別固定順序回傳);無則略過。"""
    sentences = [s_.rstrip(".") for s_ in segment_sentences(reasoning).split("\n") if s_.strip()]
    found: list[tuple[str, str]] = []
    for kind, pat in _PATTERNS.items():
        hit = next((s_ for s_ in sentences if pat.search(s_)), None)
        if hit is not None:
            found.append((kind, hit))
    return found


def main(argv: list[str] | None = None, client: SafetyClient | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pred_dir")
    ap.add_argument("case_ids", nargs="+")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "check" / "qna_probe.json"))
    ap.add_argument("--endpoint", default=CS_ENDPOINT)
    args = ap.parse_args(argv)
    pred = Path(args.pred_dir)
    index = load_policy_index()
    if client is None:
        from app.pipeline.auth import make_cs_client
        client = make_cs_client(args.endpoint)
    report: dict[str, Any] = {}
    for cid in args.case_ids:
        out = PipelineOutput.model_validate_json((pred / f"{cid}.json").read_text(encoding="utf-8"))
        bundle = ClaimBundle.model_validate_json((pred / f"{cid}.bundle.json").read_text(encoding="utf-8"))
        _, sources = build_inputs(out, bundle, index)
        row: dict[str, Any] = {}
        for kind, sentence in extract_claims(out.adjudication.reasoning):
            runs: list[str] = []
            for _ in range(args.repeat):
                try:
                    g = client.detect_groundedness(sentence, sources, task="QnA", query=QUESTIONS[kind])
                    runs.append(str(g.percentage))
                except Exception as e:  # noqa: BLE001 — 探針不中斷
                    runs.append(f"ERR {type(e).__name__}")
            row[kind] = {"sentence": sentence, "runs": runs,
                         "ungrounded_runs": sum(1 for r in runs if r[:3] != "ERR" and float(r) > 0)}
            print(f"{cid} {kind:<13} ungrounded {row[kind]['ungrounded_runs']}/{args.repeat} runs={runs} "
                  f":: {sentence[:50]}")
        report[cid] = row
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
