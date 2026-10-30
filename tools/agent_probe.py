"""SPEC-n(2026-10-02):Foundry Agent Service 探針——Repair agent 宿主可行性量測。

桌機執行(Entra 驗證,先 `az login`;不讀 AOAI_KEY):
  set FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
  python tools/agent_probe.py check/pred_route_v1 --limit 10 --mode parity --out check/probe_agent_parity.jsonl
  python tools/agent_probe.py check/pred_route_v1 --limit 10 --mode gate   --out check/probe_agent_gate.jsonl

- parity:每案兩臂同 prompt 同部署——直呼 API(既有 AOAIClient)vs Agent Service(agent_reference + strict schema),逐欄比對
- gate:agent 掛 `run_gate` function tool(客端執行 app.gate.checks.run_gate),最多 MAX_ROUNDS 輪回填;記 flags_before/after
- agent 定義每保單一個(instructions 含該保單條款原文),結束後刪除版本(--keep-agents 保留)
- SDK:azure-ai-projects 2.7.0(Agents v2:agents.create_version + PromptAgentDefinition;呼叫走 openai Responses)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import openai

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import Clause, load_policy_index  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.pipeline.aoai_client import AOAIClient, StructuredCaller  # noqa: E402
from app.pipeline.context import build_policy_context, policy_code_from_number  # noqa: E402
from app.pipeline.intake import check_documents, intake_result  # noqa: E402
from app.schemas.claim import ClaimBundle, DecisionResult  # noqa: E402

CLAIMS_DIR = ROOT / "data" / "claims"
from app.pipeline.agent_service import (  # noqa: E402
    AGENT_NAME_PREFIX,
    AGENT_REF_STYLES,
    GATE_TOOL_INSTRUCTIONS,
    GATE_TOOL_NAME,
    MAX_ROUNDS,
    AgentPool,
    AgentServiceError,
    LoopResult,
    Parsed,
    agent_ref_body,
    build_agent_definition,
    make_gate_fn,
    parse_response,
    tool_loop,
    usage_record,
)

ProbeError = AgentServiceError  # 既有測試與呼叫端名稱
__all__ = ["AGENT_NAME_PREFIX", "AGENT_REF_STYLES", "GATE_TOOL_INSTRUCTIONS", "GATE_TOOL_NAME", "MAX_ROUNDS",
           "AgentPool", "AgentServiceError", "LoopResult", "Parsed", "ProbeError", "agent_ref_body",
           "build_agent_definition", "make_gate_fn", "parse_response", "tool_loop", "usage_record"]

# ---------- 比對 ----------
def compare(a: DecisionResult, b: DecisionResult) -> dict[str, bool]:
    """判定 / 金額(程式加總)/ 明細行數 / 引文逐字(pre-mortem:RAI 改寫引文會在此暴露)。"""
    fa, fb = runner.finalize_adjudication(a.adjudication), runner.finalize_adjudication(b.adjudication)
    out = {
        "decision": fa.decision == fb.decision,
        "approved_amount": fa.approved_amount == fb.approved_amount,
        "calculation_rows": len(fa.calculation) == len(fb.calculation),
        "citations": [(c.policy_id, c.clause_no, c.quote) for c in fa.citations]
        == [(c.policy_id, c.clause_no, c.quote) for c in fb.citations],
    }
    out["identical"] = all(out.values())
    return out


# ---------- 案件材料 ----------
def case_messages(bundle_path: Path, index: dict[str, dict[str, Clause]]) -> tuple[str, str, str, ClaimBundle]:
    """既有 bundle → (policy_code, system, user, bundle);system 與 run_case 同源。"""
    bundle = ClaimBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    code = policy_code_from_number(bundle.claim_application.policy_number.value)
    if code is None:
        raise ProbeError(f"{bundle.claim_id}:無法由保單號碼歸戶")
    status, missing = check_documents(CLAIMS_DIR / bundle.claim_id)
    ir = intake_result(bundle.claim_id, status, missing, code, index)
    clauses = build_policy_context(code, "full", index)
    system, user = runner.build_decision_messages(bundle, ir, clauses)
    return code, system, user, bundle


def direct_call(aoai: StructuredCaller, system: str, user: str,
                deployment: str) -> tuple[DecisionResult, dict[str, int]]:
    obj, resp = runner._call_with_retry(aoai, system=system, user=user, model=DecisionResult,
                                        max_tokens=runner.DECISION_MAX_TOKENS, backoff_s=runner.DEFAULT_BACKOFF_S,
                                        deployment=deployment)
    usage = {"input_tokens": resp.prompt_tokens, "output_tokens": resp.completion_tokens,
             "cached_tokens": resp.cached_tokens}
    return DecisionResult.model_validate(obj), usage


# ---------- 真實用戶端(桌機) ----------
def make_project_client(endpoint: str) -> Any:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())


def summarize(rows: list[dict[str, Any]], mode: str) -> str:
    n = len(rows)
    valid = sum(1 for r in rows if r.get("valid"))
    lines = [f"探測 {n} 案;valid {valid}/{n}"]
    if mode == "parity":
        ident = sum(1 for r in rows if r.get("compare", {}).get("identical"))
        lines.append(f"identical {ident}/{n}(P2 門檻 ≥ 9/10)")
    cached = sum(1 for r in rows[1:] if sum(u["cached_tokens"] for u in r.get("agent_usage", [])) > 0)
    lines.append(f"第 2 案起 cached_tokens>0:{cached}/{max(n - 1, 0)}(P3 門檻 ≥ 5/9)")
    if mode == "gate":
        flagged = [r for r in rows if r.get("flags_before")]
        improved = sum(1 for r in flagged
                       if r.get("flags_after") is not None and len(r["flags_after"]) < len(r["flags_before"]))
        rounds = [r.get("rounds", 0) for r in rows]
        lines.append(f"有旗標案 {len(flagged)},flags_after < flags_before:{improved};"
                     f"rounds 分布 {sorted(rounds)};exhausted {sum(1 for r in rows if r.get('kind') == 'exhausted')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pred_dir", help="含 <C###>.bundle.json 的預測目錄")
    ap.add_argument("--limit", type=int, default=10, help="最多探測幾案(成本保險絲;0 = 全部)")
    ap.add_argument("--mode", choices=("parity", "gate"), default="parity")
    ap.add_argument("--deployment", default="gpt-4.1-mini", help="兩臂共用的部署名(Foundry 專案與 oai 帳戶各有一份)")
    ap.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""),
                    help="Foundry 專案 endpoint(預設讀 FOUNDRY_PROJECT_ENDPOINT)")
    ap.add_argument("--agent-ref-style", choices=AGENT_REF_STYLES, default="agent_reference")
    ap.add_argument("--keep-agents", action="store_true", help="結束後不刪 agent 版本(Portal 檢視用)")
    ap.add_argument("--out", help="每案一行 JSON")
    args = ap.parse_args(argv)
    if not args.endpoint:
        print("缺 FOUNDRY_PROJECT_ENDPOINT(或 --endpoint)", file=sys.stderr)
        return 2
    index = load_policy_index()
    bundles = sorted(Path(args.pred_dir).glob("C*.bundle.json"))
    if args.limit:
        bundles = bundles[: args.limit]
    project = make_project_client(args.endpoint)
    openai_client = project.get_openai_client()
    aoai = AOAIClient() if args.mode == "parity" else None
    pool = AgentPool(project, args.deployment, args.mode, time.strftime("%m%d%H%M"))
    rows: list[dict[str, Any]] = []
    try:
        for b in bundles:
            code, system, user, bundle = case_messages(b, index)
            ref = pool.get(code, system)
            row: dict[str, Any] = {"claim_id": bundle.claim_id, "policy": code, "mode": args.mode,
                                   "deployment": args.deployment, "agent": ref}
            t0 = time.time()
            try:
                gate_fn = make_gate_fn(bundle, index) if args.mode == "gate" else None
                lr = tool_loop(openai_client, ref, user, gate_fn, bundle.claim_id, style=args.agent_ref_style)
                row.update(kind=lr.kind, rounds=lr.rounds, agent_usage=lr.usage, response_ids=lr.response_ids,
                           valid=lr.result is not None, latency_s=round(time.time() - t0, 2))
                if lr.result is not None:
                    row["agent_decision"] = json.loads(lr.result.model_dump_json())
                if args.mode == "gate":
                    row["flags_before"] = lr.flags_before
                    if lr.result is not None:
                        final_draft = json.loads(lr.result.adjudication.model_dump_json())
                        row["flags_after"] = make_gate_fn(bundle, index)(final_draft)["flags"]
                if aoai is not None:
                    direct, du = direct_call(aoai, system, user, args.deployment)
                    row["direct_decision"], row["direct_usage"] = json.loads(direct.model_dump_json()), du
                    if lr.result is not None:
                        row["compare"] = compare(direct, lr.result)
            # 單案失敗記錄後續跑:400(agent 引用形狀)與直呼臂 finish_reason=length 皆曾讓整批中斷
            except (ProbeError, openai.OpenAIError, ValueError) as e:
                row.update(kind="error", valid=False, error=f"{type(e).__name__}: {e}"[:500],
                           latency_s=round(time.time() - t0, 2))
            rows.append(row)
            print(f"{row['claim_id']} {row['kind']} rounds={row.get('rounds', '-')} "
                  f"identical={row.get('compare', {}).get('identical', '-')} "
                  f"usage={row.get('agent_usage')} {row.get('error', '')}")
    finally:
        if not args.keep_agents:
            pool.cleanup()
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
                                  encoding="utf-8")
    print("\n" + summarize(rows, args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
