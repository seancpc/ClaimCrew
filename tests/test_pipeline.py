"""單 agent 端到端骨架(SPEC 2026-10-01)行為 1–10。

邊界(DI / Azure OpenAI / Search)一律以 fake 注入;禁止 mock 被測邏輯本身。
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.gate.policy_index import Clause, core_clause_nos, load_policy_index
from app.pipeline import aoai_client, context, di_client, intake, runner
from app.schemas.claim import (
    ClaimBundle,
    ClaimDecision,
    DecisionResult,
    IntakeStatus,
    PipelineOutput,
    to_structured_output_schema,
)

INDEX = load_policy_index()
CLAIMS = Path(__file__).resolve().parents[1] / "data" / "claims"


# ---------- fakes(邊界) ----------
class FakeRetriever:
    def __init__(self, hits: list[Clause]):
        self.hits = hits
        self.calls = 0

    def search(self, query: str, policy_code: str, k: int) -> list[Clause]:
        self.calls += 1
        return self.hits[:k]


class FakeDI:
    """回傳與 claim.json 一致的 DI 結果(custom 欄位 + layout 行)。"""

    def __init__(self, case_dir: Path):
        self.case_dir = case_dir
        self.calls: list[str] = []

    def analyze_pdf(self, model_id: str, pdf: Path) -> dict[str, Any]:
        self.calls.append(f"{model_id}:{pdf.name}")
        claim = json.loads((self.case_dir / "claim.json").read_text(encoding="utf-8"))["documents"]
        if model_id == di_client.DIAGNOSIS_MODEL_ID:
            dc = claim["diagnosis_certificate"]
            adm = dc["admissions"][0]
            fields = {f: {"value": (adm if f in ("admission_date", "discharge_date") else dc)[f], "confidence": 0.9}
                      for f in di_client.DIAGNOSIS_FIELDS}
            return {"fields": fields, "lines": [f"第1次入院日期 {adm['admission_date']}", "第2次入院日期 x"]}
        lines = [f"layout:{pdf.name}"]
        if pdf.stem == "claim_application" and claim.get("claim_application"):
            lines.append(f"保單號碼:{claim['claim_application']['policy_number']}")  # 版面文字含保單號碼
        return {"fields": {}, "lines": lines}


class FakeAOAI:
    """依 schema 名稱回傳預先準備的 JSON;可注入連續失敗次數模擬 429。"""

    def __init__(self, bundle: dict[str, Any], decision: dict[str, Any], fail_times: int = 0):
        self.bundle, self.decision, self.fail_times = bundle, decision, fail_times
        self.calls: list[dict[str, Any]] = []

    def structured_call(self, *, system: str, user: str, schema_name: str, schema: dict[str, Any],
                        max_completion_tokens: int, deployment: str | None = None) -> aoai_client.StructuredResponse:
        self.calls.append({"schema_name": schema_name, "schema": schema, "system": system, "user": user})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise aoai_client.TransientError("429 simulated")
        payload = self.bundle if schema_name == "ClaimBundle" else self.decision
        return aoai_client.StructuredResponse(
            content=json.dumps(payload, ensure_ascii=False), model="gpt-4o-2024-11-20",
            prompt_tokens=1000, completion_tokens=200,
        )


def _case(cid: str) -> Path:
    return CLAIMS / cid


def _bundle_payload(cid: str) -> dict[str, Any]:
    """以合成案的 claim.json 經 datagen 轉接層得到合法 ClaimBundle(模擬模型完美抽取)。"""
    from app.datagen.bundle import bundle_from_documents

    claim = json.loads((_case(cid) / "claim.json").read_text(encoding="utf-8"))
    bundle = bundle_from_documents(cid, claim["documents"])
    return json.loads(bundle.model_dump_json())


def _calc_line(clause_no: str, quantity: int = 1, unit_amount: str = "1000") -> dict[str, Any]:
    return {"item": "<項目>", "clause_no": clause_no, "kind": "per_unit", "quantity": quantity,
            "unit_amount": unit_amount, "actual_amount": "0", "deduction": "0", "cap": "0", "lump_amount": "0",
            "ratio": "1"}


def _decision_payload(cid: str, policy: str, clause_no: str) -> dict[str, Any]:
    clause = INDEX[policy][clause_no]
    cit = {"policy_id": policy, "clause_no": clause_no, "quote": clause.text[:20], "relevance": "test"}
    return {
        "policy": {"claim_id": cid, "citations": [cit], "not_found_aspects": []},
        # B1:LLM 只填計算明細,approved_amount 由程式加總(1 × 1000 = 1000)
        "adjudication": {"claim_id": cid, "decision": "approve", "calculation": [_calc_line(clause_no)],
                         "citations": [cit], "reasoning": "test", "confidence": 0.9},
    }


# ---------- 1–2 條款上下文 ----------
def test_full_strategy_returns_whole_policy_in_order():
    ctx = context.build_policy_context("POL-A", "full", INDEX)
    assert [c.clause_no for c in ctx] == list(INDEX["POL-A"])
    assert all(c.policy_code == "POL-A" and c.title is not None and c.text for c in ctx)


def test_core_search_strategy_is_core_union_hits_same_policy_no_dups():
    other = INDEX["POL-B"]["第7條"]
    hits = [INDEX["POL-A"]["第30條"], INDEX["POL-A"]["第2條"], other, INDEX["POL-A"]["第31條"]]
    r = FakeRetriever(hits)
    ctx = context.build_policy_context("POL-A", "core+search", INDEX, retriever=r, query="q", k=10)
    nos = [c.clause_no for c in ctx]
    assert set(core_clause_nos(INDEX["POL-A"])) <= set(nos)
    assert "第30條" in nos and "第31條" in nos
    assert len(nos) == len(set(nos)) and all(c.policy_code == "POL-A" for c in ctx)
    assert r.calls == 1


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        context.build_policy_context("POL-A", "magic", INDEX)


def test_policy_code_from_number():
    assert context.policy_code_from_number("POL-A-2026-01") == "POL-A"
    assert context.policy_code_from_number("") is None
    assert context.policy_code_from_number("XYZ") is None


def test_find_policy_code_in_layout_lines_tolerates_punctuation():
    assert context.find_policy_code_in_text(["保單號碼:POL-C-2026-07", "x"]) == "POL-C"
    assert context.find_policy_code_in_text(['"policy_number": "POL-B-2026-02",']) == "POL-B"
    assert context.find_policy_code_in_text(["POL-A", "沒有號碼"]) is None


# ---------- 3 Intake ----------
def test_intake_complete_case_lists_required_and_remedy_clauses():
    status, missing = intake.check_documents(_case("C001"))
    assert status is IntakeStatus.COMPLETE and missing == []
    res = intake.intake_result("C001", status, missing, "POL-A", INDEX)
    assert res.status is IntakeStatus.COMPLETE and res.missing_documents == []
    titles = {INDEX["POL-A"][b.split(" ", 1)[1]].title for b in res.clause_basis}
    assert titles == {"應備文件", "文件不全之補正"}


def test_intake_missing_certificate_is_incomplete(tmp_path: Path):
    case = tmp_path / "C999"
    shutil.copytree(_case("C001"), case)
    (case / "pdf" / "diagnosis_certificate.pdf").unlink()
    status, missing = intake.check_documents(case)
    assert status is IntakeStatus.INCOMPLETE and missing == ["診斷證明書"]


def test_intake_missing_application_has_empty_clause_basis(tmp_path: Path):
    case = tmp_path / "C998"
    shutil.copytree(_case("C001"), case)
    (case / "pdf" / "claim_application.pdf").unlink()
    status, missing = intake.check_documents(case)
    res = intake.intake_result("C998", status, missing, None, INDEX)
    assert res.status is IntakeStatus.INCOMPLETE and "理賠申請書" in res.missing_documents
    assert res.clause_basis == []


# ---------- 4 DI 載荷 ----------
def test_di_payload_has_only_present_documents():
    di = FakeDI(_case("C001"))
    results = di_client.extract_case(_case("C001"), di)
    payload = di_client.di_results_to_prompt_payload(results)
    assert set(payload) == {"diagnosis_certificate", "medical_receipt", "claim_application"}
    assert "incident_statement" not in payload
    assert payload["diagnosis_certificate"]["fields"]["diagnosis"]["confidence"] == 0.9
    assert any("layout:" in ln["text"] for ln in payload["medical_receipt"]["lines"])  # 行 = {text, confidence}
    assert di.calls[0].startswith(di_client.DIAGNOSIS_MODEL_ID)


def test_di_payload_includes_incident_statement_when_present():
    cid = next(d.name for d in sorted(CLAIMS.iterdir()) if (d / "pdf" / "incident_statement.pdf").is_file())
    results = di_client.extract_case(_case(cid), FakeDI(_case(cid)))
    assert "incident_statement" in di_client.di_results_to_prompt_payload(results)


# ---------- 5–6 schema ----------
def test_decision_result_schema_is_structured_output_subset():
    schema = to_structured_output_schema(DecisionResult)
    assert set(schema["properties"]) == {"policy", "adjudication"}
    assert schema["additionalProperties"] is False and schema["required"] == ["policy", "adjudication"]


def test_calls_use_single_source_schema_and_validate(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "ok"
    names = [c["schema_name"] for c in aoai.calls]
    assert names == ["ClaimBundle", "DecisionResult"]
    assert aoai.calls[0]["schema"] == to_structured_output_schema(ClaimBundle)
    assert aoai.calls[1]["schema"] == to_structured_output_schema(DecisionResult)


def test_invalid_model_output_is_named_error(tmp_path: Path):
    cid = "C001"
    bad = _bundle_payload(cid)
    bad["claim_application"]["claimed_amount"] = "-5"  # ge=0 違反
    aoai = FakeAOAI(bad, _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "error" and "ClaimBundle" in rec.error
    # SPEC-al②:報錯案輸出 manual_review(原為不輸出 → 摘要信看不到)
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert out.gate.final_decision is ClaimDecision.MANUAL_REVIEW


# ---------- 7 單案落盤 ----------
def test_run_case_writes_pipeline_output_and_run_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AOAI_KEY", "SECRET-SHOULD-NOT-LEAK")
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert out.claim_id == cid and out.gate.original_decision is ClaimDecision.APPROVE
    assert out.adjudication.approved_amount == Decimal("1000")
    lines = (tmp_path / "_run.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    assert set(row) >= {"claim_id", "strategy", "model", "prompt_tokens", "completion_tokens", "latency_s", "status"}
    assert row["prompt_tokens"] == 2000 and row["model"] == "gpt-4o-2024-11-20" and rec.status == "ok"
    assert "SECRET" not in (tmp_path / "_run.jsonl").read_text(encoding="utf-8")


def test_incomplete_case_skips_llm_and_routes_manual(tmp_path: Path):
    """C025 無診斷書:不呼叫 DI 診斷書模型與 LLM,直接 manual_review。"""
    aoai = FakeAOAI({}, {})
    di = FakeDI(_case("C025"))
    rec = runner.run_case(_case("C025"), "full", di, aoai, None, INDEX, tmp_path)
    assert rec.status == "ok" and aoai.calls == []
    out = PipelineOutput.model_validate_json((tmp_path / "C025.json").read_text(encoding="utf-8"))
    assert out.gate.final_decision is ClaimDecision.MANUAL_REVIEW
    assert out.adjudication.approved_amount == 0


# ---------- 8 重試 ----------
def test_transient_errors_are_retried_then_succeed(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"), fail_times=2)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, backoff_s=0)
    assert rec.status == "ok" and len(aoai.calls) == 4  # 2 失敗 + 2 成功


def test_persistent_errors_mark_case_error_and_route_manual(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"), fail_times=99)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, backoff_s=0)
    assert rec.status == "error" and len(aoai.calls) == runner.MAX_ATTEMPTS
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert out.gate.final_decision is ClaimDecision.MANUAL_REVIEW  # SPEC-al②


# ---------- 9 dry-run ----------
def test_dry_run_makes_no_llm_calls_but_reports_context_size(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, dry_run=True)
    assert aoai.calls == [] and rec.status == "dry_run" and rec.context_clauses == len(INDEX["POL-A"])
    assert not (tmp_path / f"{cid}.json").exists()


# ---------- 10 property ----------
@settings(max_examples=60, deadline=None)
@given(
    policy=st.sampled_from(["POL-A", "POL-B", "POL-C"]),
    picks=st.lists(st.integers(min_value=0, max_value=40), max_size=12),
)
def test_core_search_context_bounded_by_core_and_policy(policy: str, picks: list[int]):
    clauses = list(INDEX[policy].values())
    hits = [clauses[i % len(clauses)] for i in picks]
    ctx = context.build_policy_context(policy, "core+search", INDEX, retriever=FakeRetriever(hits), query="q", k=10)
    nos = [c.clause_no for c in ctx]
    assert set(core_clause_nos(INDEX[policy])) <= set(nos) <= set(INDEX[policy])
    assert len(nos) == len(set(nos))
    full = context.build_policy_context(policy, "full", INDEX)
    assert context.estimate_tokens(full) >= context.estimate_tokens(ctx)


# ---------- 2026-10-01 C008 真實觸發後補的行為 ----------
def test_layout_lines_carry_min_word_confidence():
    """Layout 回應的行附最低字詞信心(以 spans 對應 words),表格列同理;供 Extraction 照抄 DI 信心。"""
    raw = {
        "pages": [{
            "lines": [{"content": "保單號碼 POL-A-2026-01", "spans": [{"offset": 0, "length": 18}]}],
            "words": [
                {"content": "保單號碼", "confidence": 0.99, "span": {"offset": 0, "length": 4}},
                {"content": "POL-A-2026-01", "confidence": 0.87, "span": {"offset": 5, "length": 13}},
            ],
        }],
        "tables": [{"cells": [
            {"rowIndex": 0, "columnIndex": 0, "content": "病房費", "spans": [{"offset": 0, "length": 3}]},
            {"rowIndex": 0, "columnIndex": 1, "content": "1,000", "spans": [{"offset": 5, "length": 5}]},
        ]}],
    }
    res = di_client.normalize_analyze_result(raw)
    assert res["lines"][0] == {"text": "保單號碼 POL-A-2026-01", "confidence": 0.87}
    assert res["lines"][1]["text"] == "表格列:病房費 | 1,000" and 0 < res["lines"][1]["confidence"] <= 1


def test_run_case_also_persists_bundle_for_analysis(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    bundle = ClaimBundle.model_validate_json((tmp_path / f"{cid}.bundle.json").read_text(encoding="utf-8"))
    assert bundle.claim_id == cid


def test_decision_system_prompt_tells_policy_id_format(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    system2 = aoai.calls[1]["system"]
    assert "policy_id" in system2 and "POL-A" in system2 and "保單號碼" in system2


# ---------- 8b 429 退避(2026-10-01 全量首跑實測:capacity 10 × 1/2/4 秒退避 → 72 案幾乎全錯) ----------
def test_retry_after_header_parsing():
    f = aoai_client.retry_after_from_headers
    assert f({"retry-after": "12"}) == 12.0
    assert f({"Retry-After": "3"}) == 3.0  # 大小寫不敏感
    assert f({"retry-after-ms": "500"}) == 0.5
    assert f({}) is None
    assert f({"retry-after": "soon"}) is None


def test_wait_seconds_honors_retry_after_and_caps():
    w = runner._wait_seconds
    assert w(0, 1.0, None) == 1.0 and w(3, 1.0, None) == 8.0  # 指數退避
    assert w(0, 1.0, 7.0) == 7.0  # 伺服器 Retry-After 較長 → 聽伺服器
    assert w(4, 1.0, 7.0) == 16.0  # 退避較長 → 取較長者
    assert w(10, 1.0, None) == runner.MAX_BACKOFF_S and w(0, 1.0, 999.0) == runner.MAX_BACKOFF_S  # 上限


def test_runner_sleeps_retry_after_from_transient_error(tmp_path: Path, monkeypatch):
    cid = "C001"
    slept: list[float] = []
    monkeypatch.setattr(runner.time, "sleep", lambda s: slept.append(s))

    class RetryAfterAOAI(FakeAOAI):
        def structured_call(self, **kw: Any) -> aoai_client.StructuredResponse:
            if self.fail_times > 0:
                self.fail_times -= 1
                self.calls.append(kw)
                raise aoai_client.TransientError("429 simulated", retry_after_s=9.0)
            return super().structured_call(**kw)

    aoai = RetryAfterAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"), fail_times=1)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, backoff_s=1.0)
    assert rec.status == "ok" and slept == [9.0]


def test_max_attempts_covers_a_full_rate_limit_window():
    # 預設退避下總等待 ≥ 60s(TPM 以分鐘為窗),單案不會在一個窗內就放棄
    assert runner.MAX_ATTEMPTS >= 6
    total = sum(runner._wait_seconds(i, runner.DEFAULT_BACKOFF_S, None) for i in range(runner.MAX_ATTEMPTS - 1))
    assert total >= 60


# ---------- B1-2 金額由程式加總(SPEC 2026-10-01-b) ----------
def test_runner_sums_calculation_and_ignores_model_total(tmp_path: Path):
    cid = "C001"
    payload = _decision_payload(cid, "POL-A", "第7條")
    payload["adjudication"]["calculation"] = [_calc_line("第7條", 6, "2000"), _calc_line("第11條", 6, "1000")]
    payload["adjudication"]["reasoning"] = "合計 14000"  # 模型文字說 14000,程式以明細 18000 為準
    aoai = FakeAOAI(_bundle_payload(cid), payload)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "ok"
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert out.adjudication.approved_amount == Decimal("18000")
    assert len(out.adjudication.calculation) == 2


def test_runner_zeroes_amount_on_deny_even_if_calculation_present(tmp_path: Path):
    cid = "C001"
    payload = _decision_payload(cid, "POL-A", "第7條")
    payload["adjudication"]["decision"] = "deny"  # 明細仍帶 1 行 → 程式仍歸 0
    aoai = FakeAOAI(_bundle_payload(cid), payload)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "ok"
    out = PipelineOutput.model_validate_json((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))
    assert out.adjudication.approved_amount == 0


def test_model_output_with_approved_amount_is_rejected():
    # LLM 草稿不得含 approved_amount(schema 封閉;模型抄數路徑被型別堵死)
    from pydantic import ValidationError

    payload = _decision_payload("C001", "POL-A", "第7條")
    payload["adjudication"]["approved_amount"] = "1000"
    with pytest.raises(ValidationError):
        DecisionResult.model_validate(payload)


# ---------- P1-4 derived_facts 注入呼叫 2(SPEC 2026-10-01-c) ----------
def test_decision_user_message_carries_derived_facts(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "ok"
    user2 = json.loads(aoai.calls[1]["user"])
    assert set(user2) == {"intake", "claim_bundle", "derived_facts"}
    df = user2["derived_facts"]
    claim = json.loads((_case(cid) / "claim.json").read_text(encoding="utf-8"))
    adm = claim["documents"]["diagnosis_certificate"]["admissions"][0]
    from datetime import date
    expect = (date.fromisoformat(adm["discharge_date"]) - date.fromisoformat(adm["admission_date"])).days + 1
    assert df["admissions"][0]["days_inclusive"] == expect and df["total_days"] == expect
    assert df["warnings"] == [] and isinstance(df["days_since_effective"], int)
    assert "derived_facts" in aoai.calls[1]["system"]  # 系統訊息要告訴模型這段事實的用途


# ---------- DI 結果磁碟快取(2026-10-01 F0 月額度用罄;同一 PDF 的 OCR 為確定性,不應每輪重抽) ----------
def test_di_cache_hits_on_same_pdf_and_model_and_misses_otherwise(tmp_path: Path):
    from app.pipeline.di_client import CachedAnalyzer

    class Counting:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def analyze_pdf(self, model_id: str, pdf: Path) -> dict[str, Any]:
            self.calls.append((model_id, pdf.name))
            return {"fields": {}, "lines": [{"text": f"{model_id}:{pdf.name}", "confidence": 1.0}]}

    pdf_a = tmp_path / "a.pdf"
    pdf_a.write_bytes(b"%PDF-A")
    pdf_b = tmp_path / "b.pdf"
    pdf_b.write_bytes(b"%PDF-B")
    inner = Counting()
    cached = CachedAnalyzer(inner, tmp_path / "cache")
    r1 = cached.analyze_pdf("prebuilt-layout", pdf_a)
    r2 = cached.analyze_pdf("prebuilt-layout", pdf_a)  # 命中:不再呼叫
    assert r1 == r2 and len(inner.calls) == 1
    cached.analyze_pdf("diagnosis-cert-v3", pdf_a)  # 模型不同 → 未命中
    cached.analyze_pdf("prebuilt-layout", pdf_b)  # 內容不同 → 未命中
    assert len(inner.calls) == 3
    # 鍵以內容為準:同內容不同檔名命中;內容改變未命中
    same = tmp_path / "renamed.pdf"
    same.write_bytes(b"%PDF-A")
    cached.analyze_pdf("prebuilt-layout", same)
    assert len(inner.calls) == 3
    pdf_a.write_bytes(b"%PDF-A2")
    cached.analyze_pdf("prebuilt-layout", pdf_a)
    assert len(inner.calls) == 4
    assert cached.hits == 2 and cached.misses == 4
    assert sorted(p.suffix for p in (tmp_path / "cache").rglob("*.json")) == [".json"] * 4


def test_di_cache_survives_process_and_ignores_corrupt_entry(tmp_path: Path):
    from app.pipeline.di_client import CachedAnalyzer

    class Once:
        n = 0

        def analyze_pdf(self, model_id: str, pdf: Path) -> dict[str, Any]:
            Once.n += 1
            return {"fields": {"x": {"value": "1", "confidence": 0.9}}, "lines": []}

    pdf = tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-C")
    CachedAnalyzer(Once(), tmp_path / "cache").analyze_pdf("prebuilt-layout", pdf)
    again = CachedAnalyzer(Once(), tmp_path / "cache")  # 新實例(模擬下一輪執行)讀同一目錄
    assert again.analyze_pdf("prebuilt-layout", pdf)["fields"]["x"]["value"] == "1" and Once.n == 1
    for f in (tmp_path / "cache").rglob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    # 壞檔視為未命中並覆寫
    assert again.analyze_pdf("prebuilt-layout", pdf)["fields"]["x"]["value"] == "1" and Once.n == 2


# ---------- ModelOutputError 重試一次(2026-10-02 v4 C064:同案 v3 正常,屬偶發亂填) ----------
def test_model_output_error_is_retried_once_then_succeeds(tmp_path: Path):
    cid = "C001"
    good = _decision_payload(cid, "POL-A", "第7條")
    bad = json.loads(json.dumps(good))
    bad["adjudication"]["calculation"][0]["actual_amount"] = "abc"  # 第一次:驗證失敗

    class FlakyAOAI(FakeAOAI):
        def __init__(self) -> None:
            super().__init__(_bundle_payload(cid), good)
            self.decision_calls = 0

        def structured_call(self, **kw: Any) -> aoai_client.StructuredResponse:
            self.calls.append(kw)
            if kw["schema_name"] == "DecisionResult":
                self.decision_calls += 1
                payload = bad if self.decision_calls == 1 else good
            else:
                payload = self.bundle
            return aoai_client.StructuredResponse(content=json.dumps(payload, ensure_ascii=False),
                                                  model="gpt-4o-2024-11-20", prompt_tokens=1, completion_tokens=1)

    aoai = FlakyAOAI()
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "ok" and aoai.decision_calls == 2


def test_model_output_error_twice_marks_case_error(tmp_path: Path):
    cid = "C001"
    bad = _decision_payload(cid, "POL-A", "第7條")
    bad["adjudication"]["calculation"][0]["actual_amount"] = "abc"
    aoai = FakeAOAI(_bundle_payload(cid), bad)
    rec = runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path)
    assert rec.status == "error" and "ModelOutputError" in rec.error
    assert sum(1 for c in aoai.calls if c["schema_name"] == "DecisionResult") == 2  # 重試一次即止


# --- SPEC 2026-10-02-k:逐呼叫 deployment 路由 + 逐呼叫 token 記帳(方案 X,ADR-007 實測基礎)---
class RoutingAOAI(FakeAOAI):
    """記錄每次呼叫收到的 deployment;回傳可區分呼叫的 token 數與 cached_tokens。"""

    def __init__(self, bundle: dict[str, Any], decision: dict[str, Any], fail_times: int = 0,
                 bad_decision_times: int = 0):
        super().__init__(bundle, decision, fail_times)
        self.deployments: list[str | None] = []
        self.bad_decision_times = bad_decision_times

    def structured_call(self, **kw: Any) -> aoai_client.StructuredResponse:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise aoai_client.TransientError("429 simulated")
        self.deployments.append(kw.get("deployment"))
        if kw["schema_name"] == "ClaimBundle":
            return aoai_client.StructuredResponse(content=json.dumps(self.bundle, ensure_ascii=False),
                                                  model="m-extract", prompt_tokens=1000, completion_tokens=100,
                                                  cached_tokens=600)
        if self.bad_decision_times > 0:
            self.bad_decision_times -= 1
            return aoai_client.StructuredResponse(content="{}", model="m-decide", prompt_tokens=1, completion_tokens=1)
        return aoai_client.StructuredResponse(content=json.dumps(self.decision, ensure_ascii=False),
                                              model="m-decide", prompt_tokens=500, completion_tokens=50,
                                              cached_tokens=0)


def _run_routed(tmp_path: Path, aoai: RoutingAOAI, **kw: Any) -> runner.RunRecord:
    cid = "C001"
    return runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, backoff_s=0.0, **kw)


def test_k1_each_call_uses_its_own_deployment(tmp_path: Path):
    aoai = RoutingAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"))
    rec = _run_routed(tmp_path, aoai, extraction_deployment="mini-x", decision_deployment="gpt-4o")
    assert rec.status == "ok" and aoai.deployments == ["mini-x", "gpt-4o"]


def test_k1_default_passes_none_so_client_default_applies(tmp_path: Path):
    aoai = RoutingAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"))
    rec = _run_routed(tmp_path, aoai)
    assert rec.status == "ok" and aoai.deployments == [None, None]


def test_k2_per_call_tokens_recorded_and_totals_unchanged(tmp_path: Path):
    aoai = RoutingAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"))
    rec = _run_routed(tmp_path, aoai)
    names = [c["name"] for c in rec.calls]
    assert names == ["extraction", "decision"]
    ext, dec = rec.calls
    def quad(c: dict[str, Any]) -> tuple[Any, ...]:
        return (c["model"], c["prompt_tokens"], c["cached_tokens"], c["completion_tokens"])

    assert quad(ext) == ("m-extract", 1000, 600, 100)
    assert quad(dec) == ("m-decide", 500, 0, 50)
    assert all(c["latency_s"] >= 0 for c in rec.calls)
    # 既有合計欄位不變:prompt = 兩次之和、model = 呼叫 1 回報
    assert (rec.model, rec.prompt_tokens, rec.completion_tokens) == ("m-extract", 1500, 150)
    line = json.loads((tmp_path / runner.RUN_LOG).read_text(encoding="utf-8").splitlines()[-1])
    assert [c["name"] for c in line["calls"]] == ["extraction", "decision"] and line["prompt_tokens"] == 1500


def test_k2_retry_does_not_duplicate_call_records(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    aoai = RoutingAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"), fail_times=1,
                       bad_decision_times=1)
    rec = _run_routed(tmp_path, aoai)
    assert rec.status == "ok" and len(rec.calls) == 2  # 429 重試一次 + 判定驗證失敗重試一次,仍各記一筆
    assert rec.calls[1]["prompt_tokens"] == 500  # 記成功那次,不是失敗的 1


def test_k2_error_case_keeps_only_successful_calls(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    aoai = RoutingAOAI(_bundle_payload("C001"), _decision_payload("C001", "POL-A", "第7條"),
                       bad_decision_times=runner.MODEL_OUTPUT_RETRIES + 1)
    rec = _run_routed(tmp_path, aoai)
    assert rec.status == "error" and [c["name"] for c in rec.calls] == ["extraction"]


def test_k2_structured_response_cached_tokens_defaults_to_zero():
    r = aoai_client.StructuredResponse(content="{}", model="m", prompt_tokens=1, completion_tokens=1)
    assert r.cached_tokens == 0
