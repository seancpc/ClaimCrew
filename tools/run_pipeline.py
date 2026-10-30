"""單 agent 端到端執行入口(SPEC 2026-10-01):DI → 抽取整理 → Intake → 條款上下文 → 判定 → Gate → 落盤。

用法(桌機,venv 內;金鑰只進環境變數):
    $env:DI_KEY     = (az cognitiveservices account keys list -n di-claimcrew  -g rg-claimcrew --query key1 -o tsv)
    $env:AOAI_KEY   = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
    $env:SEARCH_KEY = (az search admin-key show --service-name srch-claimcrew -g rg-claimcrew --query primaryKey -o tsv)
    python tools/run_pipeline.py C008 --strategy full --out check/pred_full
    python tools/run_pipeline.py --all --strategy core+search --out check/pred_core --limit 5
    python tools/run_pipeline.py --all --strategy full --out check/pred_full --dry-run   # 不呼叫 LLM,只報上下文條數
DI 結果預設快取於 check/di_cache/<模型>/<PDF 雜湊>.json(F0 月額度保護;--di-cache "" 停用)
之後:python -m eval.run_eval check/pred_full

輸出:<out>/<C###>.json(PipelineOutput)、<out>/_run.jsonl(每案 token / 耗時 / 模型 / 策略 / 狀態)。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import load_policy_index  # noqa: E402
from app.pipeline.aoai_client import StructuredCaller, StructuredResponse  # noqa: E402
from app.pipeline.auth import auth_mode, make_aoai_client, make_di_client  # noqa: E402
from app.pipeline.context import STRATEGIES, Retriever  # noqa: E402
from app.pipeline.di_client import CachedAnalyzer, DIClient, DocumentAnalyzer  # noqa: E402
from app.pipeline.runner import RUN_LOG, run_case  # noqa: E402
from app.pipeline.search_client import SearchRetriever  # noqa: E402
from app.review.card import REVIEW_ACTION_ENV, DigestCollector  # noqa: E402

CLAIMS_DIR = ROOT / "data" / "claims"
DI_ENDPOINT = os.environ.get("DI_ENDPOINT", "https://di-claimcrew.cognitiveservices.azure.com/")
CS_ENDPOINT = os.environ.get("CS_ENDPOINT", "https://cs-claimcrew.cognitiveservices.azure.com/")


def resolve_cases(case_ids: list[str], *, all_cases: bool, cases_dir: Path = CLAIMS_DIR) -> list[Path]:
    """案件目錄:--all 取 cases_dir 下所有含 claim.json 的子目錄;否則依案號
    (SPEC-v 第二段:--cases-dir 可指向 check/ 下的注入變體)。"""
    if all_cases:
        return sorted(d for d in cases_dir.iterdir() if d.is_dir() and (d / "claim.json").is_file())
    return [cases_dir / c for c in case_ids]


def review_run_tag(explicit: str | None, out_dir: Path) -> str:
    """摘要主旨標記:明確值優先,否則輸出資料夾名(雲端 Job 的資料夾恆為 out,主旨才需 run_id)。"""
    return explicit or out_dir.name


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10).stdout


def code_version() -> str:
    """<短 SHA>[+dirty](僅計已追蹤檔的改動);無 git 或非 repo(雲端映像)時改讀建置注入的 GIT_SHA,皆無則 unknown。"""
    try:
        sha = _git("rev-parse", "--short", "HEAD").strip()
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no").strip())
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("GIT_SHA") or "unknown"
    return f"{sha}+dirty" if dirty else sha


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_ids", nargs="*", help="案號,如 C008;與 --all 擇一")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--strategy", choices=STRATEGIES, default="full",
                    help="條款上下文策略;2026-10-01 A/B(56 案配對)定案 full 為預設:"
                         "core+search 只省 6.5%% prompt tokens 且判定略差")
    ap.add_argument("--out", required=True, help="預測輸出目錄(建議 check/ 下,不入版控)")
    ap.add_argument("--limit", type=int, default=0, help="最多跑幾案(成本保險絲)")
    ap.add_argument("--dry-run", action="store_true", help="不呼叫 LLM,只建上下文並報條數")
    ap.add_argument("-k", type=int, default=10, help="core+search 的檢索 top-k")
    ap.add_argument("--pace", type=float, default=0.0, help="每案之間停幾秒(TPM 配額緩衝;0 不停)")
    ap.add_argument("--extraction-deployment", default="gpt-4.1-mini",
                    help="抽取呼叫的部署名;ADR-007 accepted(2026-10-02)預設 gpt-4.1-mini,回歸對照時改 gpt-4o")
    ap.add_argument("--decision-deployment", default=None, help="SPEC-k:判定呼叫的部署名;預設用 gpt-4o")
    ap.add_argument("--repair", action="store_true",
                    help="SPEC-o:Gate 旗標案跑 Repair agent(Agent Service;需 FOUNDRY_PROJECT_ENDPOINT + az login)")
    ap.add_argument("--repair-deployment", default="gpt-4o", help="Repair agent 與第二意見的部署名")
    ap.add_argument("--review", action="store_true",
                    help="SPEC-r/s:收集 manual_review 案的覆核卡片,跑完一封摘要 POST 到 $REVIEW_WEBHOOK_URL")
    ap.add_argument("--safety", action="store_true",
                    help="SPEC-v:Gate 後呼叫 Content Safety(groundedness + 四類掃描)記入 safety 影子欄位;不改判定")
    ap.add_argument("--cases-dir", default=str(CLAIMS_DIR),
                    help="案件根目錄(預設 data/claims;注入探針用 check/injection)")
    ap.add_argument("--run-tag", default=None,
                    help="SPEC-u:摘要信主旨的執行標記(cloud_run 傳 run_id);省略時用輸出資料夾名")
    ap.add_argument("--di-cache", default=str(ROOT / "check" / "di_cache"),
                    help="DI 結果磁碟快取目錄(同 PDF 同模型只抽一次;F0 月額度保護);空字串停用")
    args = ap.parse_args(argv)

    cases = resolve_cases(args.case_ids, all_cases=args.all, cases_dir=Path(args.cases_dir))
    if not cases:
        print("沒有指定案件(給案號或 --all)", file=sys.stderr)
        return 2
    if args.limit:
        cases = cases[: args.limit]
    entra = auth_mode() == "entra"  # SPEC-t:雲端 Job 以託管身分,不需金鑰
    missing_keys = [] if entra else [k for k in ("DI_KEY", "AOAI_KEY") if not os.environ.get(k)]
    if args.strategy == "core+search" and not os.environ.get("SEARCH_KEY"):
        missing_keys.append("SEARCH_KEY")
    if args.review and not os.environ.get("REVIEW_WEBHOOK_URL"):
        missing_keys.append("REVIEW_WEBHOOK_URL")
    if args.safety and not entra and not os.environ.get("CS_KEY"):
        missing_keys.append("CS_KEY")
    if missing_keys and not args.dry_run:
        print(f"缺環境變數 {missing_keys}(見檔頭用法)", file=sys.stderr)
        return 2

    index = load_policy_index()
    di: DocumentAnalyzer = make_di_client(DI_ENDPOINT) if not args.dry_run else DIClient(DI_ENDPOINT, key="dry-run")
    cache: CachedAnalyzer | None = None
    if args.di_cache:
        cache = CachedAnalyzer(di, Path(args.di_cache))
        di = cache
    aoai: StructuredCaller
    if args.dry_run:  # noqa: SIM108 — 三元式會讓 mypy 把兩個無共同基底的類別 join 成 object
        aoai = _NoCallClient()
    else:
        aoai = make_aoai_client()
    retriever: Retriever | None = SearchRetriever(index) if args.strategy == "core+search" else None
    out_dir = Path(args.out)
    repairer = None
    pool = None
    if args.repair:
        from tools.rerepair import make_repairer  # 延後 import:不開 --repair 不需要 azure-ai-projects
        repairer, pool = make_repairer(index, aoai, args.repair_deployment)

    reviewer: DigestCollector | None = None
    if args.review:  # SPEC-s:逐案收集、跑完一封摘要(逐案寄送在 Outlook.com 第 6 封即被封鎖)
        reviewer = DigestCollector(review_run_tag(args.run_tag, out_dir),
                                   action_url=os.environ.get(REVIEW_ACTION_ENV) or None)  # SPEC-am

    safety = None
    if args.safety:  # SPEC-v 影子模式
        from app.pipeline.auth import make_cs_client
        from app.pipeline.safety import attach_safety
        cs = make_cs_client(CS_ENDPOINT)
        safety = lambda o, b: attach_safety(o, b, cs, index)  # noqa: E731

    version = code_version()
    print(f"程式版本:{version}")
    ok = err = 0
    pt = ct = 0
    for i, case_dir in enumerate(cases):
        if i and args.pace > 0:
            time.sleep(args.pace)
        rec = run_case(case_dir, args.strategy, di, aoai, retriever, index, out_dir, dry_run=args.dry_run, k=args.k,
                       extraction_deployment=args.extraction_deployment,
                       decision_deployment=args.decision_deployment, repairer=repairer, reviewer=reviewer,
                       safety=safety, code_version=version)
        ok += rec.status in ("ok", "dry_run")
        err += rec.status == "error"
        pt += rec.prompt_tokens
        ct += rec.completion_tokens
        tail = (rec.error if rec.status == "error"
                else f"ctx={rec.context_clauses} tok={rec.prompt_tokens}+{rec.completion_tokens}")
        if rec.review != "off":
            tail += f" review={rec.review}"
        print(f"{rec.claim_id} {rec.status:<7} {rec.latency_s:>6.1f}s {tail}")
    if pool is not None:
        pool.cleanup()
    if reviewer is not None:
        posts = reviewer.flush(os.environ["REVIEW_WEBHOOK_URL"], out_dir)
        for r in posts:
            print(f"覆核摘要:{r.status} http={r.http_status} run={r.run_id} {r.error}".rstrip())
        if not posts:
            print("覆核摘要:無 manual_review 案,未寄送")
    cache_note = f";DI 快取 命中 {cache.hits} / 未命中 {cache.misses}({args.di_cache})" if cache else ""
    print(f"\n完成 {ok} / 失敗 {err};tokens prompt={pt} completion={ct};記錄 {out_dir / RUN_LOG}{cache_note}")
    return 1 if err else 0


class _NoCallClient:
    """dry-run 用:任何呼叫即失敗(保證不打 API)。"""

    def structured_call(self, *, system: str, user: str, schema_name: str, schema: dict[str, object],
                        max_completion_tokens: int, deployment: str | None = None) -> StructuredResponse:
        raise RuntimeError("dry-run 不應呼叫 LLM")


if __name__ == "__main__":
    sys.exit(main())
