"""單案執行器(SPEC 2026-10-01 行為 7、8、9):DI → 呼叫 1 → Intake → 條款上下文 → 呼叫 2 → Gate → 落盤。

- 缺件案不呼叫 LLM,直接依應備/補正條款產出 manual_review(與 eval selfcheck 的形狀一致)
- 失敗不落半套:先寫暫存檔再 rename;`_run.jsonl` 每案一列(欄位白名單,無金鑰)
- 可重試錯誤指數退避,最多 MAX_ATTEMPTS 次(每次呼叫各自計);429 聽伺服器 Retry-After,單次等待上限 MAX_BACKOFF_S
  (2026-10-01 全量首跑實測:capacity 10K TPM × 3 次 1/2/4 秒退避 → 72 案幾乎全為 429 錯誤)
"""

from __future__ import annotations

import functools
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.gate.calc import normalize_calculation, total_amount
from app.gate.checks import EXTRACTION_FIELD_CONFIDENCE_THRESHOLD, run_gate
from app.gate.policy_index import (
    Clause,
    clause_segments,
    load_policy_index,
    normalize_clause_no,
    normalize_policy_id,
    normalize_segment,
)
from app.pipeline import di_client
from app.pipeline.aoai_client import StructuredCaller, StructuredResponse, TransientError
from app.pipeline.context import (
    Retriever,
    build_policy_context,
    find_policy_code_in_text,
    policy_code_from_number,
    render_context,
)
from app.pipeline.derived import derive_facts
from app.pipeline.intake import check_documents, intake_result
from app.schemas.claim import (
    AdjudicationDraft,
    AdjudicationResult,
    ClaimBundle,
    ClaimDecision,
    DecisionResult,
    GateFlag,
    GateResult,
    IntakeResult,
    IntakeStatus,
    PipelineOutput,
    PolicyCitation,
    to_structured_output_schema,
)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "agents" / "prompts"
MAX_ATTEMPTS = 6
MODEL_OUTPUT_RETRIES = 1  # 模型輸出未過 Pydantic 驗證時的重試次數
MAX_BACKOFF_S = 60.0
DEFAULT_BACKOFF_S = 2.0  # 2+4+8+16+32 = 62s ≥ 一個 TPM 分鐘窗
EXTRACTION_MAX_TOKENS = 4000
DECISION_MAX_TOKENS = 4000
RUN_LOG = "_run.jsonl"


@dataclass
class RunRecord:
    claim_id: str
    strategy: str
    status: str  # ok | error | dry_run
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    context_clauses: int = 0
    intake_status: str = ""
    error: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)  # SPEC-k 逐呼叫記帳(只記成功那次)
    review: str = "off"  # SPEC-r:off | sent | skipped | error:<msg>(無 --review 時恆 off)
    code_version: str = ""  # 執行時程式版本:<短 SHA>[+dirty] / unknown(runbook W21:防跑在舊程式而不自知)


class ModelOutputError(ValueError):
    """模型輸出未通過 Pydantic 驗證(具名 schema + 原文片段)。"""


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _wait_seconds(attempt: int, backoff_s: float, retry_after_s: float | None) -> float:
    """取「指數退避」與「伺服器 Retry-After」較長者,並以 MAX_BACKOFF_S 封頂。"""
    return float(min(MAX_BACKOFF_S, max(backoff_s * (2**attempt), retry_after_s or 0.0)))


def _call_with_retry(aoai: StructuredCaller, *, system: str, user: str, model: type[BaseModel],
                     max_tokens: int, backoff_s: float,
                     deployment: str | None = None) -> tuple[BaseModel, StructuredResponse]:
    schema_name = model.__name__
    schema = to_structured_output_schema(model)
    last: Exception | None = None
    output_failures = 0
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = aoai.structured_call(system=system, user=user, schema_name=schema_name,
                                        schema=schema, max_completion_tokens=max_tokens, deployment=deployment)
        except TransientError as e:
            last = e
            time.sleep(_wait_seconds(attempt, backoff_s, e.retry_after_s))
            continue
        try:
            return model.model_validate_json(resp.content), resp
        except ValidationError as e:
            # 2026-10-02 v4 C064:同案 v3 正常,偶發亂填(「ゼロ」);驗證失敗重試一次,再失敗才定案為錯
            output_failures += 1
            if output_failures > MODEL_OUTPUT_RETRIES:
                raise ModelOutputError(f"{schema_name} 驗證失敗:{e.errors()[:3]};原文片段:{resp.content[:200]}") from e
            continue
    raise TransientError(f"{schema_name} 連續 {MAX_ATTEMPTS} 次可重試錯誤:{last}")


def _incomplete_output(claim_id: str, missing: list[str], clause_basis: list[str],
                       index: dict[str, dict[str, Clause]]) -> AdjudicationResult:
    cits = []
    for basis in clause_basis:
        pol, cn = basis.split(" ", 1)
        cits.append(PolicyCitation(policy_id=pol, clause_no=cn, quote=index[pol][cn].text.strip()[:20],
                                   relevance="文件不全之應備/補正依據"))
    return AdjudicationResult(
        claim_id=claim_id, decision=ClaimDecision.MANUAL_REVIEW, approved_amount=Decimal(0),
        citations=cits, reasoning=f"文件不全,缺:{'、'.join(missing)};依應備文件與補正條款轉人工補件",
        confidence=1.0,
    )


def output_json(output: PipelineOutput) -> str:
    """選配欄位(repair / safety)為 None 時不落欄位:未開啟的輸出與開啟前位元組相同(SPEC-o / SPEC-v)。"""
    exclude = {f for f in ("repair", "safety") if getattr(output, f) is None}
    return output.model_dump_json(indent=2, exclude=exclude or None)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _call_record(name: str, resp: StructuredResponse, started: float) -> dict[str, Any]:
    return {"name": name, "model": resp.model, "prompt_tokens": resp.prompt_tokens,
            "cached_tokens": resp.cached_tokens, "completion_tokens": resp.completion_tokens,
            "latency_s": round(time.time() - started, 2)}


def _repair_record(usage: list[dict[str, int]], started: float) -> dict[str, Any]:
    """SPEC-o:Repair 可能多次呼叫(工具回填),合計成一筆記帳。"""
    return {"name": "repair", "model": "", "prompt_tokens": sum(u.get("input_tokens", 0) for u in usage),
            "cached_tokens": sum(u.get("cached_tokens", 0) for u in usage),
            "completion_tokens": sum(u.get("output_tokens", 0) for u in usage),
            "latency_s": round(time.time() - started, 2)}


Reviewer = Callable[[PipelineOutput, ClaimBundle | None], str]

RAW_TEXT_MARKER = "原文片段"


def error_output(claim_id: str, exc: Exception) -> PipelineOutput:
    """SPEC-al②:報錯案轉人工(原本只記 _run.jsonl、不輸出不送卡 → 摘要信看不到;SPEC-ak 重度劣化 9/13 案)。

    detail 截掉 ModelOutputError 的「原文片段」:可能含病患姓名,而此時無 bundle 可遮罩(固定解法 21);
    完整訊息只留本地 log。"""
    head = f"{type(exc).__name__}: {exc}".split(RAW_TEXT_MARKER)[0].rstrip()
    detail = (head[:-1] if head.endswith((";", ";")) else head)[:300]
    flag = GateFlag(check="pipeline_error", detail=f"{detail}(管線未完成,需人工依原始文件審核)")
    adj = AdjudicationResult(claim_id=claim_id, decision=ClaimDecision.MANUAL_REVIEW, approved_amount=Decimal(0),
                             citations=[], reasoning="管線執行失敗,未產出判定;見 Gate 旗標 pipeline_error",
                             confidence=0.0)
    gate = GateResult(claim_id=claim_id, original_decision=ClaimDecision.MANUAL_REVIEW,
                      final_decision=ClaimDecision.MANUAL_REVIEW, flags=[flag], passed=False)
    return PipelineOutput(claim_id=claim_id, adjudication=adj, gate=gate)


def _review_hook(rec: RunRecord, output: PipelineOutput, bundle: ClaimBundle | None,
                 reviewer: Reviewer | None) -> None:
    """SPEC-r:Gate 後送人工覆核卡片;reviewer 回 sent/skipped,任何例外只記 error(遮罩 sig=),不影響 status。"""
    if reviewer is None:
        rec.review = "off"
        return
    from app.review.card import mask_url, should_review  # 延後 import:避免 runner ↔ review 循環
    if not should_review(output):
        rec.review = "skipped"
        return
    try:
        rec.review = reviewer(output, bundle)
    except Exception as e:  # noqa: BLE001 — 送卡失敗不得中斷管線
        rec.review = mask_url(f"error: {type(e).__name__}: {e}")[:300]


def _append_run_log(out_dir: Path, rec: RunRecord) -> None:
    with (out_dir / RUN_LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def run_case(
    case_dir: Path,
    strategy: str,
    di: di_client.DocumentAnalyzer,
    aoai: StructuredCaller,
    retriever: Retriever | None,
    index: dict[str, dict[str, Clause]],
    out_dir: Path,
    *,
    dry_run: bool = False,
    backoff_s: float = DEFAULT_BACKOFF_S,
    k: int = 10,
    extraction_deployment: str | None = None,  # SPEC-k:呼叫 1(抽取)部署名,None 用用戶端預設
    decision_deployment: str | None = None,  # SPEC-k:呼叫 2(判定)部署名
    repairer: Callable[[PipelineOutput, ClaimBundle], PipelineOutput] | None = None,  # SPEC-o:Gate 後修補
    reviewer: Reviewer | None = None,  # SPEC-r:Gate 後送人工覆核卡片
    safety: Callable[[PipelineOutput, ClaimBundle], PipelineOutput] | None = None,  # SPEC-v:影子 groundedness
    code_version: str = "",
) -> RunRecord:
    claim_id = case_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = RunRecord(claim_id=claim_id, strategy=strategy, status="ok", code_version=code_version)
    t0 = time.time()
    try:
        status, missing = check_documents(case_dir)
        rec.intake_status = status.value
        if status is IntakeStatus.INCOMPLETE:
            # 缺件:不呼叫 DI/LLM。保單歸戶僅在申請書存在時可得,此處以 None 處理(申請書缺 → 無依據)
            policy_code = None
            if "理賠申請書" not in missing:
                policy_code = _policy_code_from_application(case_dir, di)
            ir = intake_result(claim_id, status, missing, policy_code, index)
            adj = _incomplete_output(claim_id, missing, ir.clause_basis, index)
            bundle = None
        else:
            di_results = di_client.extract_case(case_dir, di)
            payload = di_client.di_results_to_prompt_payload(di_results)
            if dry_run:
                rec.status = "dry_run"
                policy_code = _policy_code_from_payload(payload)
                rec.context_clauses = len(build_policy_context(policy_code, strategy, index, retriever,
                                                               query=claim_id, k=k)) if policy_code else 0
                rec.latency_s = round(time.time() - t0, 2)
                _append_run_log(out_dir, rec)
                return rec
            user1 = json.dumps({"claim_id": claim_id, "documents": payload}, ensure_ascii=False)
            system1 = (f"{_prompt('extraction')}\n\n# 系統參數\n"
                       f"EXTRACTION_FIELD_CONFIDENCE_THRESHOLD = {EXTRACTION_FIELD_CONFIDENCE_THRESHOLD}"
                       "(規則 5 之門檻數值;低於此之欄位路徑列入 low_confidence_fields)\n")
            t1 = time.time()
            bundle_obj, r1 = _call_with_retry(aoai, system=system1, user=user1,
                                              model=ClaimBundle, max_tokens=EXTRACTION_MAX_TOKENS,
                                              backoff_s=backoff_s, deployment=extraction_deployment)
            rec.calls.append(_call_record("extraction", r1, t1))
            bundle = ClaimBundle.model_validate(bundle_obj)
            _write_atomic(out_dir / f"{claim_id}.bundle.json", bundle.model_dump_json(indent=2))  # 供信心分布分析
            rec.model, rec.prompt_tokens, rec.completion_tokens = r1.model, r1.prompt_tokens, r1.completion_tokens
            policy_code = policy_code_from_number(bundle.claim_application.policy_number.value)
            if policy_code is None:
                raise ModelOutputError(f"無法由保單號碼歸戶:{bundle.claim_application.policy_number.value!r}")
            ir = intake_result(claim_id, status, missing, policy_code, index)
            clauses = build_policy_context(policy_code, strategy, index, retriever,
                                           query=_retrieval_query(bundle), k=k)
            rec.context_clauses = len(clauses)
            system2, user2 = build_decision_messages(bundle, ir, clauses)
            t2 = time.time()
            decision_obj, r2 = _call_with_retry(aoai, system=system2, user=user2, model=DecisionResult,
                                                max_tokens=DECISION_MAX_TOKENS, backoff_s=backoff_s,
                                                deployment=decision_deployment)
            rec.calls.append(_call_record("decision", r2, t2))
            decision = DecisionResult.model_validate(decision_obj)
            rec.prompt_tokens += r2.prompt_tokens
            rec.completion_tokens += r2.completion_tokens
            adj = finalize_adjudication(decision.adjudication, bundle)
        gate = run_gate(adj, bundle, index) if bundle is not None else _gate_without_bundle(adj, index)
        output = PipelineOutput(claim_id=claim_id, adjudication=adj, gate=gate)
        if repairer is not None and bundle is not None:
            t3 = time.time()
            output = repairer(output, bundle)
            if output.repair is not None and output.repair.invoked:
                rec.calls.append(_repair_record(output.repair.usage, t3))
        # repair 為 None 時不落該欄位:無 --repair 的輸出與 SPEC-o 之前位元組相同
        if safety is not None and bundle is not None:  # SPEC-v 影子欄位:Gate / Repair 後、覆核前;不改判定
            output = safety(output, bundle)
        _write_atomic(out_dir / f"{claim_id}.json", output_json(output))
        if bundle is not None:
            _review_hook(rec, output, bundle, reviewer)
    except (TransientError, ModelOutputError, di_client.DIError, ValueError) as e:
        rec.status = "error"
        rec.error = f"{type(e).__name__}: {e}"[:500]
        err_out = error_output(claim_id, e)
        _write_atomic(out_dir / f"{claim_id}.json", output_json(err_out))
        _review_hook(rec, err_out, None, reviewer)
    rec.latency_s = round(time.time() - t0, 2)
    _append_run_log(out_dir, rec)
    return rec


def build_decision_messages(bundle: ClaimBundle, ir: IntakeResult, clauses: list[Clause]) -> tuple[str, str]:
    """呼叫 2(判定)的 system / user 訊息;run_case 與 tools/agent_probe 共用同一份,確保探針與主路徑同 prompt(SPEC-n)。"""
    system = (
        f"{_prompt('policy')}\n\n---\n\n{_prompt('adjudication')}\n\n---\n\n"
        "# 本次執行方式\n單 agent 骨架:條款檢索已由系統完成,適用保單之條款如下列(逐字原文)。"
        "你只能引用下列條款;quote 必須是下列原文的連續片段。Intake 結果亦附於使用者訊息。\n"
        "引用格式:`policy_id` 填條款標頭【】內的**保單代號**(如 POL-A),**不是**申請書上的保單號碼"
        "(如 POL-A-2026-01);`clause_no` 填標頭內的條號原樣(如 第7條 / 附錄一)。\n"
        "使用者訊息另附 `derived_facts`(程式算好的日數、段落間隔、距生效日天數、ICD 是否傷害章):"
        "所有日數與天數**一律引用該段數值**,不得自行由日期計算;`warnings` 列出的欄位代表無法解析,視為未知。\n\n"
        f"{render_context(clauses)}"
    )
    user = json.dumps({"intake": json.loads(ir.model_dump_json()),
                       "claim_bundle": json.loads(bundle.model_dump_json()),
                       "derived_facts": json.loads(derive_facts(bundle).model_dump_json())},
                      ensure_ascii=False)
    return system, user


@functools.cache
def _policy_index() -> dict[str, dict[str, Clause]]:
    return load_policy_index()


def fill_citation_quotes(citations: list[PolicyCitation]) -> list[PolicyCitation]:
    """SPEC-aj 程式填引文:segment 能在該條對上 → quote 換成條款庫原文;segment 空(歷史輸出)或對不上 → 原樣
    (對不上交 Gate `citation_segment_not_found` 改派,不在此吞掉)。"""
    out = []
    for c in citations:
        clause = _policy_index().get(normalize_policy_id(c.policy_id), {}).get(normalize_clause_no(c.clause_no))
        text = clause_segments(clause).get(normalize_segment(c.segment)) if clause and c.segment.strip() else None
        out.append(c.model_copy(update={"quote": text}) if text is not None else c)
    return out


def finalize_adjudication(draft: AdjudicationDraft, bundle: ClaimBundle | None = None, *,
                          merge_split_lines: bool = True) -> AdjudicationResult:
    """LLM 草稿 → 判定結果:approve 時 approved_amount = 程式加總,其餘一律 0(B1-2)。

    SPEC-p P1 合併同限額拆行:2026-10-03 八組離線量測 v7 C069 合併後消掉 cap-sum 旗標、露出漏雜費行成放行少付
    −5,200 → 曾預設停用;SPEC-q 收據覆蓋檢核(`receipt_category_uncovered`)補上該洞後**預設開啟**
    (八組重測:放行錯 0、誤旗標 0)。`merge_split_lines=False` 保留為量測用 opt-out。
    """
    lines = draft.calculation
    if merge_split_lines:
        groups = len(derive_facts(bundle).stay_groups) if bundle is not None else 1
        lines = normalize_calculation(draft.calculation, groups=max(1, groups))
    amount = total_amount(lines) if draft.decision is ClaimDecision.APPROVE else Decimal(0)
    # SPEC-x 顧問欄位:僅 manual_review 保留;其餘判定由程式清空,不信任模型(無放行權,不影響 Gate / 核付)
    manual = draft.decision is ClaimDecision.MANUAL_REVIEW
    undisputed = list(draft.undisputed_calculation) if manual else []
    advisory = {"undisputed_calculation": undisputed, "disputed_items": list(draft.disputed_items) if manual else []}
    cits = fill_citation_quotes(draft.citations)
    fields: dict[str, Any] = {**draft.model_dump(), "calculation": lines, "citations": cits, **advisory}
    return AdjudicationResult(**fields, approved_amount=amount,
                              undisputed_amount=total_amount(undisputed) if undisputed else Decimal(0))


def _gate_without_bundle(adj: AdjudicationResult, index: dict[str, dict[str, Clause]]) -> Any:
    """缺件案沒有 ClaimBundle 可做金額/信心檢核,只做引用存在性檢核。"""
    from app.gate.checks import _check_citations, routes_to_manual  # 與 run_gate 同源的引用檢核
    from app.schemas.claim import GateResult

    flags = _check_citations(adj, index)
    final = adj.decision if not routes_to_manual(flags) else ClaimDecision.MANUAL_REVIEW
    return GateResult(claim_id=adj.claim_id, original_decision=adj.decision, final_decision=final,
                      flags=flags, passed=not flags)


def _policy_code_from_application(case_dir: Path, di: di_client.DocumentAnalyzer) -> str | None:
    """缺件但申請書在:以 layout 行文字找保單號碼歸戶(不呼叫 LLM)。"""
    res = di.analyze_pdf(di_client.LAYOUT_MODEL_ID, case_dir / "pdf" / "claim_application.pdf")
    return find_policy_code_in_text([str(ln) for ln in res.get("lines", [])])


def _policy_code_from_payload(payload: dict[str, Any]) -> str | None:
    return find_policy_code_in_text([str(ln) for ln in payload.get("claim_application", {}).get("lines", [])])


def _retrieval_query(bundle: ClaimBundle) -> str:
    dc, rc, ap = bundle.diagnosis_certificate, bundle.medical_receipt, bundle.claim_application
    parts = [ap.claim_type.value, dc.diagnosis.value, dc.treatment_summary.value]
    parts += [it.item_name for it in rc.items]
    if bundle.incident_statement is not None:
        parts.append(bundle.incident_statement.statement_text.value)
    return ";".join(p for p in parts if p)
