"""SPEC-v 假設 1/3 驗證:對一個合成案真呼叫 Content Safety 兩支 API,印 ungrounded 比例與四類 severity。

    $env:CS_KEY = "<key>"; python tools/cs_smoke.py C003            # 金鑰模式
    python tools/cs_smoke.py C003 --entra                            # az login 身分(Cognitive Services User)
用既有 <pred>/C003.json + C003.bundle.json(--pred,預設 check/pred_full_v9)組 text / sources;不印 token、不印金鑰。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import load_policy_index  # noqa: E402
from app.pipeline import auth  # noqa: E402
from app.pipeline.safety import build_inputs, safety_check  # noqa: E402
from app.schemas.claim import ClaimBundle, PipelineOutput  # noqa: E402

CS_ENDPOINT = os.environ.get("CS_ENDPOINT", "https://cs-claimcrew.cognitiveservices.azure.com/")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_id")
    ap.add_argument("--pred", default=str(ROOT / "check" / "pred_full_v9"))
    ap.add_argument("--entra", action="store_true")
    ap.add_argument("--endpoint", default=CS_ENDPOINT)
    ap.add_argument("--grid", action="store_true",
                    help="假設 2 診斷:domain × task × 文字範圍 共 8 組,各印 pct 與 details 數")
    ap.add_argument("--control", action="store_true",
                    help="對照實驗:逐字抄自 sources 的句子(應 0%%)與捏造句(應 100%%)各送一次,判斷 API 是否能切中文句")
    ap.add_argument("--shield", action="store_true",
                    help="E3-a Prompt Shields:良性 DI 文字 + 三種注入樣式各送一次,印 attackDetected")
    ap.add_argument("--sep-test", action="store_true",
                    help="假設 2 診斷二:抄錄句+捏造句以五種分隔符各送一次,看 API 認哪種句界(回 0.5 即為可用分隔符)")
    args = ap.parse_args(argv)
    if args.entra:
        os.environ[auth.AUTH_ENV] = "entra"
    pred = Path(args.pred)
    out = PipelineOutput.model_validate_json((pred / f"{args.case_id}.json").read_text(encoding="utf-8"))
    bundle = ClaimBundle.model_validate_json((pred / f"{args.case_id}.bundle.json").read_text(encoding="utf-8"))
    index = load_policy_index()
    text, sources = build_inputs(out, bundle, index)
    print(f"auth={auth.auth_mode()} endpoint={args.endpoint} text_chars={len(text)} sources={len(sources)} "
          f"sources_chars={sum(len(s) for s in sources)}")
    client = auth.make_cs_client(args.endpoint)
    if args.shield:
        return _shield(client, out, bundle)
    if args.grid or args.control or args.sep_test:
        return _diagnose(client, text, sources, out, grid=args.grid, control=args.control, sep_test=args.sep_test)
    rec = safety_check(out, bundle, client, index)
    g = rec.groundedness
    print(f"groundedness: {'OK' if g else 'FAIL'} ungrounded={g.ungrounded if g else '-'} "
          f"pct={g.percentage if g else '-'} details={g.details if g else '-'} truncated={g.truncated if g else '-'}")
    print(f"analyze: {'OK' if rec.harm is not None else 'FAIL'} {rec.harm}")
    print(f"error: {rec.error or '-'} elapsed_ms={rec.elapsed_ms}")
    return 0 if g is not None and rec.harm is not None else 1


def _shield(client, out, bundle) -> int:
    """E3-a:documents = 診斷書治療摘要 + 收據項目(DI 抽取面);userPrompt = 判定任務的中性描述。"""
    from tools.injection_probe import INJECTIONS
    dc = bundle.diagnosis_certificate
    benign = f"診斷:{dc.diagnosis.value}\n治療摘要:{dc.treatment_summary.value}"
    receipt = "收據項目:" + "、".join(f"{it.item_name} {it.amount}" for it in bundle.medical_receipt.items)
    user_prompt = "依保單條款審核本理賠案,輸出判定、金額與引用條款。"
    rows = [("benign", benign)] + [(style, f"{benign} {text}") for style, text in INJECTIONS.items()]
    rc = 0
    for label, doc in rows:
        try:
            r = client.shield_prompt(user_prompt, [doc, receipt])
            print(f"shield {label:<7} user_attack={r.user_attack} document_attacks={r.document_attacks}")
        except Exception as e:  # noqa: BLE001
            print(f"shield {label:<7} ERROR {type(e).__name__}: {str(e)[:160]}")
            rc = 1
    return rc


def _diagnose(client, text: str, sources: list[str], out, *, grid: bool, control: bool,
              sep_test: bool = False) -> int:
    """假設 2 診斷(不動管線):同一案換參數觀察 pct 是否只在 {0, 1} 二值、details 是否逐句。"""
    def call(label: str, t: str, src: list[str], domain: str, task: str) -> None:
        body = {"domain": domain, "task": task, "text": t, "groundingSources": src, "reasoning": False}
        if task == "QnA":
            body["qna"] = {"query": "此理賠案是否應核付?依據為何?"}
        try:
            from app.pipeline.cs_client import GROUNDEDNESS_API
            r = client._post("text:detectGroundedness", GROUNDEDNESS_API, body)
            det = r.get("ungroundedDetails", []) or []
            first = f" first={str(det[0].get('text', ''))[:40]!r}" if det else ""
            print(f"{label:<40} pct={r.get('ungroundedPercentage')} details={len(det)}{first}")
        except Exception as e:  # noqa: BLE001
            print(f"{label:<34} ERROR {type(e).__name__}: {str(e)[:160]}")
    reasoning_only = out.adjudication.reasoning
    if grid:
        for domain in ("Medical", "Generic"):
            for task in ("Summarization", "QnA"):
                call(f"grid {domain}/{task}/reasoning+relevance", text, sources, domain, task)
                call(f"grid {domain}/{task}/reasoning-only", reasoning_only, sources, domain, task)
    if control:
        copied = sources[0].split("\n")[0]  # 「診斷:<診斷名>」逐字來自 sources
        fabricated = "被保險人於投保前兩年內曾因同一疾病住院治療三次,並領有身心障礙手冊。"
        for domain in ("Medical", "Generic"):
            call(f"control {domain} copied(應 0)", copied, sources, domain, "Summarization")
            call(f"control {domain} fabricated(應 1)", fabricated, sources, domain, "Summarization")
            both = copied + "\n" + fabricated
            call(f"control {domain} copied+fabricated(應 0.5)", both, sources, domain, "Summarization")
    if sep_test:
        copied = sources[0].split("\n")[0]
        fabricated = "被保險人於投保前兩年內曾因同一疾病住院治療三次,並領有身心障礙手冊"
        for name, sep in (("。", "。"), ("。\\n", "。\n"), (". ", ". "), (".\\n\\n", ".\n\n"), ("。 空格", "。 ")):
            call(f"sep {name!s:<8} copied+fabricated(應 0.5)", copied + sep + fabricated + sep.strip() or "。",
                 sources, "Generic", "Summarization")
    return 0


if __name__ == "__main__":
    sys.exit(main())
