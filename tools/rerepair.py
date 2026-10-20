"""SPEC-o(2026-10-03):離線重放 Repair——讀既有預測目錄,只對 Gate 旗標案(plan ≠ skip)呼叫 Repair agent,
其餘原樣複製到輸出目錄;輸出目錄可直接 `python -m eval.run_eval <out>`。

桌機(Entra 驗證 + 第二意見用既有 oai 帳戶金鑰):
  $env:FOUNDRY_PROJECT_ENDPOINT = "https://fdy-claimcrew.services.ai.azure.com/api/projects/claimcrew"
  $env:AOAI_KEY = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
  python tools\\rerepair.py check\\pred_full_v8 --out check\\pred_full_v8_repaired --limit 3     # 先 3 案(高風險假設 1)
  python tools\\rerepair.py check\\pred_full_v8 --out check\\pred_full_v8_repaired
  python -m eval.run_eval check\\pred_full_v8_repaired > check\\eval_full_v8_repaired.json
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import Clause, load_policy_index  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.pipeline.agent_service import AgentPool  # noqa: E402
from app.pipeline.aoai_client import AOAIClient, StructuredCaller  # noqa: E402
from app.pipeline.repair import (  # noqa: E402
    REPAIR_INSTRUCTIONS,
    decision_context,
    dump_output,
    plan_repair,
    repair_case,
)
from app.schemas.claim import AdjudicationResult, ClaimBundle, DecisionResult, PipelineOutput  # noqa: E402

REPAIR_PREFIX = "claimcrew-repair"
Repairer = Callable[[PipelineOutput, ClaimBundle], PipelineOutput]


def make_project_client(endpoint: str) -> Any:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())


def second_opinion_fn(aoai: StructuredCaller, bundle: ClaimBundle, index: dict[str, dict[str, Clause]],
                      deployment: str) -> Callable[[], AdjudicationResult]:
    """第二意見:原判定 prompt 直呼(無修補脈絡),只取判定與金額比對。"""
    def call() -> AdjudicationResult:
        _code, ir, clauses = decision_context(bundle, index)
        system, user = runner.build_decision_messages(bundle, ir, clauses)
        obj, _resp = runner._call_with_retry(aoai, system=system, user=user, model=DecisionResult,
                                             max_tokens=runner.DECISION_MAX_TOKENS, backoff_s=runner.DEFAULT_BACKOFF_S,
                                             deployment=deployment)
        return runner.finalize_adjudication(DecisionResult.model_validate(obj).adjudication, bundle)
    return call


def make_repairer(index: dict[str, dict[str, Clause]], aoai: StructuredCaller | None, deployment: str,
                  endpoint: str | None = None, style: str = "agent_reference",
                  advisory: bool = True) -> tuple[Repairer, AgentPool]:
    """建 Repair 用的 Agent Service 用戶端與 agent 池;回傳 (repairer, pool),呼叫端負責 pool.cleanup()。"""
    ep = endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not ep:
        raise SystemExit("缺 FOUNDRY_PROJECT_ENDPOINT(或 --endpoint)")
    project = make_project_client(ep)
    client = project.get_openai_client()
    pool = AgentPool(project, deployment, "repair", time.strftime("%m%d%H%M"), prefix=REPAIR_PREFIX)

    def repairer(output: PipelineOutput, bundle: ClaimBundle) -> PipelineOutput:
        if output.gate.passed or plan_repair(output.gate).kind == "skip":
            return repair_case(output, bundle, index, None, {}, second_opinion_fn=lambda: output.adjudication)
        code, ir, clauses = decision_context(bundle, index)
        system, _user = runner.build_decision_messages(bundle, ir, clauses)
        ref = pool.get(code, system + REPAIR_INSTRUCTIONS)
        so = (second_opinion_fn(aoai, bundle, index, deployment) if aoai is not None
              else (lambda: output.adjudication))
        return repair_case(output, bundle, index, client, ref, second_opinion_fn=so, style=style, advisory=advisory)

    return repairer, pool


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pred_dir", help="含 <C###>.json 與 <C###>.bundle.json 的預測目錄")
    ap.add_argument("--out", required=True, help="輸出目錄(全部 72 案;未修補者原樣複製)")
    ap.add_argument("--limit", type=int, default=-1, help="最多修補幾案(-1 全部;0 = 只複製不修)")
    ap.add_argument("--deployment", default="gpt-4o", help="Repair agent 與第二意見的部署名")
    ap.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""))
    ap.add_argument("--agent-ref-style", default="agent_reference")
    ap.add_argument("--keep-agents", action="store_true")
    ap.add_argument("--release", action="store_true",
                    help="SPEC-p P5:量測用放行路徑(守門全過即取代判定);預設為顧問模式,只記建議不改派")
    args = ap.parse_args(argv)
    index = load_policy_index()
    pred, out_dir = Path(args.pred_dir), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in pred.glob("C*.json") if not p.name.endswith(".bundle.json"))
    aoai = AOAIClient()
    repairer, pool = make_repairer(index, aoai, args.deployment, endpoint=args.endpoint, style=args.agent_ref_style,
                                   advisory=not args.release)
    reasons: Counter[str] = Counter()
    plans: Counter[str] = Counter()
    invoked = 0
    try:
        for f in files:
            output = PipelineOutput.model_validate_json(f.read_text(encoding="utf-8"))
            bundle_path = pred / f"{f.stem}.bundle.json"
            if bundle_path.exists():
                shutil.copy2(bundle_path, out_dir / bundle_path.name)
            plan = plan_repair(output.gate).kind if not output.gate.passed else "skip"
            plans[plan] += 1
            within_limit = args.limit < 0 or invoked < args.limit
            # skip 案也經過 repairer(不呼叫 agent,只留 invoked=False 記錄,與 run_case --repair 路徑一致);
            # 可修案超過 --limit 時原樣複製(無記錄)
            if bundle_path.exists() and (plan == "skip" or within_limit):
                bundle = ClaimBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
                t0 = time.time()
                output = repairer(output, bundle)
                r = output.repair
                if r is not None and r.invoked:
                    invoked += 1
                    reasons[r.guard_reason] += 1
                    print(f"{f.stem} plan={plan} rounds={r.rounds} reason={r.guard_reason} "
                          f"{output.gate.final_decision.value} {output.adjudication.approved_amount} "
                          f"{round(time.time() - t0, 1)}s {r.error[:80] if r.error else ''}")
            (out_dir / f.name).write_text(dump_output(output), encoding="utf-8")
    finally:
        if not args.keep_agents:
            pool.cleanup()
    plan_txt = " / ".join(f"{k} {v}" for k, v in sorted(plans.items()))
    reason_txt = " / ".join(f"{k} {v}" for k, v in sorted(reasons.items()))
    print(f"\n{len(files)} 案;plan {plan_txt};invoked {invoked};守門 {reason_txt}")
    print(f"輸出 {out_dir};eval:python -m eval.run_eval {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
