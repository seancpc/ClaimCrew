"""SPEC 2026-10-02-m:Policy 呼叫單獨探針(X-c 假設 1)。

以既有 <pred_dir>/<C###>.bundle.json 為輸入,只打 Policy 呼叫(policy.md + 條款全文,PolicyResult schema),
量 clause recall(對真值 clause_basis,與 eval 同一正規化)與引用有效率(validate_citation 無旗標)。
不呼叫 DI、不跑抽取、不寫 _run.jsonl、不動 Gate。

用法:
    python3 tools/policy_probe.py check/pred_route_v1 --limit 10 --deployment gpt-4o --out check/probe_policy_10.jsonl
判讀門檻(SPEC-m 寫死):宏平均 recall ≥ 0.90 → X-c 72 案;0.80–0.90 → 再 20 案;< 0.80 → 走 Y。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.checks import validate_citation  # noqa: E402
from app.gate.policy_index import Clause, load_policy_index, normalize_clause_no, normalize_policy_id  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.pipeline.aoai_client import AOAIClient, StructuredCaller, StructuredResponse  # noqa: E402
from app.pipeline.context import build_policy_context, policy_code_from_number, render_context  # noqa: E402
from app.pipeline.intake import check_documents, intake_result  # noqa: E402
from app.schemas.claim import ClaimBundle, IntakeResult, PolicyResult  # noqa: E402
from eval.run_eval import load_truths  # noqa: E402

CLAIMS_DIR = ROOT / "data" / "claims"
POLICY_MAX_TOKENS = 4000


def build_policy_messages(bundle: ClaimBundle, ir: IntakeResult, clauses: list[Clause]) -> tuple[str, str]:
    """system = policy.md + 執行方式段 + 條款原文;user = {claim_id, intake, claim_bundle}。不含 adjudication.md。"""
    system = (
        f"{runner._prompt('policy')}\n\n---\n\n"
        "# 本次執行方式\n條款檢索已由系統完成,適用保單之條款如下列(逐字原文)。"
        "你只能引用下列條款;quote 必須是下列原文的連續片段。Intake 結果附於使用者訊息。\n"
        "引用格式:`policy_id` 填條款標頭【】內的**保單代號**(如 POL-A),**不是**申請書上的保單號碼;"
        "`clause_no` 填標頭的條號。\n\n"
        f"# 適用保單條款\n{render_context(clauses)}"
    )
    user = json.dumps({"claim_id": bundle.claim_id, "intake": json.loads(ir.model_dump_json()),
                       "claim_bundle": json.loads(bundle.model_dump_json())}, ensure_ascii=False)
    return system, user


@dataclass
class ProbeScore:
    recall: float
    valid: float
    n_citations: int
    not_found_aspects: list[str]


def _basis_key(policy_id: str, clause_no: str) -> str:
    return f"{normalize_policy_id(policy_id)} {normalize_clause_no(clause_no)}"


def score(result: PolicyResult, truth: dict[str, Any], index: dict[str, dict[str, Clause]]) -> ProbeScore:
    """recall = 真值 clause_basis 被引用覆蓋比例;valid = validate_citation 無旗標之引用 / 引用總數。"""
    truth_basis = {_basis_key(*b.split(" ", 1)) for b in truth.get("clause_basis", [])}
    pred_basis = {_basis_key(c.policy_id, c.clause_no) for c in result.citations}
    recall = len(truth_basis & pred_basis) / len(truth_basis) if truth_basis else 0.0
    n = len(result.citations)
    valid = sum(1 for c in result.citations if validate_citation(c, index) is None) / n if n else 0.0
    return ProbeScore(recall=recall, valid=valid, n_citations=n, not_found_aspects=list(result.not_found_aspects))


def probe_case(bundle_path: Path, aoai: StructuredCaller, index: dict[str, dict[str, Clause]],
               deployment: str | None = None) -> tuple[PolicyResult, StructuredResponse]:
    bundle = ClaimBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    code = policy_code_from_number(bundle.claim_application.policy_number.value)
    if code is None:
        raise ValueError(f"{bundle.claim_id}:無法由保單號碼歸戶")
    status, missing = check_documents(CLAIMS_DIR / bundle.claim_id)
    ir = intake_result(bundle.claim_id, status, missing, code, index)
    clauses = build_policy_context(code, "full", index)
    system, user = build_policy_messages(bundle, ir, clauses)
    obj, resp = runner._call_with_retry(aoai, system=system, user=user, model=PolicyResult,
                                        max_tokens=POLICY_MAX_TOKENS, backoff_s=runner.DEFAULT_BACKOFF_S,
                                        deployment=deployment)
    return PolicyResult.model_validate(obj), resp


def make_client() -> StructuredCaller:
    return AOAIClient()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pred_dir", help="含 <C###>.bundle.json 的預測目錄")
    ap.add_argument("--limit", type=int, default=0, help="最多探測幾案(成本保險絲;0 = 全部)")
    ap.add_argument("--deployment", default="gpt-4o", help="Policy 呼叫部署名(預設 gpt-4o)")
    ap.add_argument("--out", help="每案一行 JSON(含 citations)供離線複查")
    args = ap.parse_args(argv)
    index = load_policy_index()
    truths = load_truths()
    aoai = make_client()
    bundles = sorted(Path(args.pred_dir).glob("C*.bundle.json"))
    all_cases = {p.name[:4] for p in Path(args.pred_dir).glob("C*.json") if not p.name.endswith(".bundle.json")}
    skipped = len(all_cases - {b.name[:4] for b in bundles})
    if args.limit:
        bundles = bundles[: args.limit]
    rows: list[str] = []
    scores: list[ProbeScore] = []
    pt = ct = cc = 0
    for b in bundles:
        cid = b.name[:4]
        result, resp = probe_case(b, aoai, index, deployment=args.deployment)
        s = score(result, truths[cid], index)
        scores.append(s)
        pt += resp.prompt_tokens
        ct += resp.completion_tokens
        cc += resp.cached_tokens
        print(f"{cid} recall={s.recall:.3f} valid={s.valid:.3f} n_cit={s.n_citations} "
              f"not_found={s.not_found_aspects} tok={resp.prompt_tokens}+{resp.completion_tokens}")
        rows.append(json.dumps({"claim_id": cid, "model": resp.model, **asdict(s),
                                "citations": [json.loads(c.model_dump_json()) for c in result.citations]},
                               ensure_ascii=False))
    if args.out:
        Path(args.out).write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    n = len(scores)
    mr = sum(s.recall for s in scores) / n if n else 0.0
    mv = sum(s.valid for s in scores) / n if n else 0.0
    print(f"\n探測 {n} 案(跳過 {skipped} 案無 bundle);宏平均 recall={mr:.3f} valid={mv:.3f};"
          f"tokens prompt={pt} cached={cc} completion={ct}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
