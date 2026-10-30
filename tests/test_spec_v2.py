"""SPEC-v 第二段(ADR-009 證據補強):W1 Prompt Shields client、W2 groundedness QnA 模式、W3 resafety --repeat/--tag、
W4 QnA 逐主張探針、W5 注入變體產生器、W6 run_pipeline --cases-dir。探針真跑在桌機;此處驗工具的確定性行為。"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.pipeline import cs_client
from app.schemas.claim import GroundednessResult
from tests.test_entra_auth import _Resp
from tests.test_review import _c055
from tests.test_spec_v import _capture, _FakeCS

SHIELD = {"userPromptAnalysis": {"attackDetected": False},
          "documentsAnalysis": [{"attackDetected": False}, {"attackDetected": True}]}
GROUNDED = {"ungroundedDetected": False, "ungroundedPercentage": 0, "ungroundedDetails": []}


# ---------- W1 Prompt Shields ----------
def test_w1_shield_prompt_request_shape_and_result(monkeypatch):
    seen = _capture(monkeypatch, [_Resp(SHIELD)])
    r = cs_client.CSClient("https://cs.x", key="k").shield_prompt("<使用者提示>", ["<文件一>", "<文件二>"])
    assert seen[0].full_url == f"https://cs.x/contentsafety/text:shieldPrompt?api-version={cs_client.SHIELD_API}"
    assert json.loads(seen[0].data) == {"userPrompt": "<使用者提示>", "documents": ["<文件一>", "<文件二>"]}
    assert r.user_attack is False and r.document_attacks == [False, True] and r.any_attack is True
    seen2 = _capture(monkeypatch, [_Resp({"userPromptAnalysis": {"attackDetected": True}, "documentsAnalysis": []})])
    r2 = cs_client.CSClient("https://cs.x", key="k").shield_prompt("<越獄提示>", [])
    assert r2.user_attack is True and r2.document_attacks == [] and r2.any_attack is True and len(seen2) == 1


def test_w1_shield_prompt_4xx_raises_cserror(monkeypatch):
    import io
    import urllib.error
    _capture(monkeypatch, [urllib.error.HTTPError("https://cs", 400, "err", {}, io.BytesIO(b"{}"))])  # type: ignore[arg-type]
    with pytest.raises(cs_client.CSError):
        cs_client.CSClient("https://cs.x", key="k").shield_prompt("p", ["d"])


# ---------- W2 groundedness QnA 模式 ----------
def test_w2_groundedness_qna_mode_sends_task_and_query(monkeypatch):
    seen = _capture(monkeypatch, [_Resp(GROUNDED)])
    cs_client.CSClient("https://cs.x", key="k").detect_groundedness("<答案句>", ["<源頭>"], task="QnA", query="<問句>")
    body = json.loads(seen[0].data)
    assert body["task"] == "QnA" and body["qna"] == {"query": "<問句>"} and body["text"] == "<答案句>"


def test_w2_groundedness_summarization_has_no_qna_block(monkeypatch):
    seen = _capture(monkeypatch, [_Resp(GROUNDED)])
    cs_client.CSClient("https://cs.x", key="k").detect_groundedness("t", ["s"])
    assert "qna" not in json.loads(seen[0].data)
    with pytest.raises(ValueError):
        cs_client.CSClient("https://cs.x", key="k").detect_groundedness("t", ["s"], task="QnA")  # QnA 必須帶 query


# ---------- W3 resafety --repeat / --tag ----------
def test_w3_resafety_repeat_writes_tagged_sidecars_and_keeps_main_untouched(tmp_path):
    from app.pipeline import runner
    from tools import resafety
    out, bundle = _c055()
    (tmp_path / "C055.json").write_text(runner.output_json(out), encoding="utf-8")
    (tmp_path / "C055.bundle.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    (tmp_path / "C055.safety.json").write_text("{\"invoked\": true}", encoding="utf-8")
    fake = _FakeCS("0.3")
    assert resafety.main([str(tmp_path), "--repeat", "3", "--tag", "rep"], client=fake) == 0
    assert sorted(p.name for p in tmp_path.glob("C055.safety.rep*.json")) == [
        "C055.safety.rep1.json", "C055.safety.rep2.json", "C055.safety.rep3.json"]
    assert (tmp_path / "C055.safety.json").read_text(encoding="utf-8") == "{\"invoked\": true}"  # 正式 sidecar 不動
    assert len([c for c in fake.calls if c[0] == "g"]) == 3


# ---------- W4 QnA 逐主張探針 ----------
def test_w4_extract_claims_picks_pre_existing_days_and_waiting_sentences():
    from tools.cs_qna_probe import QUESTIONS, extract_claims
    reasoning = ("文件齊全。診斷書記載病患有十年糖尿病病史,屬既往症。住院日數經確認為六日。"
                 "事件距生效日 218 日,等待期已過。判定 approve。")
    claims = extract_claims(reasoning)
    kinds = [k for k, _ in claims]
    assert kinds == ["pre_existing", "days", "waiting"]
    assert "既往症" in claims[0][1] and "六日" in claims[1][1] and "等待期" in claims[2][1]
    assert set(QUESTIONS) == {"pre_existing", "days", "waiting"}
    assert all(q.endswith(("?", "?")) for q in QUESTIONS.values())
    assert extract_claims("文件齊全。判定 approve。") == []
    assert len(extract_claims("A 既往症。B 既往症。C 既往症。D 既往症。")) == 1  # 每類最多一句(最先出現)


def test_w4_probe_main_runs_claims_per_case_with_repeats_and_writes_json(tmp_path):
    from app.pipeline import runner
    from tools import cs_qna_probe
    out, bundle = _c055()
    adj = out.adjudication.model_copy(update={"reasoning": "診斷書記載既往症。住院共 9 日。等待期已過。"})
    out = out.model_copy(update={"adjudication": adj})
    (tmp_path / "C055.json").write_text(runner.output_json(out), encoding="utf-8")
    (tmp_path / "C055.bundle.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    class QnAFake:
        def __init__(self):
            self.calls: list[Any] = []

        def detect_groundedness(self, text, sources, domain="Medical", *, task="Summarization", query=None):
            self.calls.append((text, task, query))
            pct = Decimal("1") if "既往" in text else Decimal("0")
            return GroundednessResult(ungrounded=pct > 0, percentage=pct, details=[text] if pct else [])
    fake = QnAFake()
    argv = [str(tmp_path), "C055", "--repeat", "2", "--out", str(tmp_path / "qna.json")]
    assert cs_qna_probe.main(argv, client=fake) == 0
    assert len(fake.calls) == 6 and all(c[1] == "QnA" and c[2] for c in fake.calls)
    rep = json.loads((tmp_path / "qna.json").read_text(encoding="utf-8"))
    row = rep["C055"]
    assert row["pre_existing"]["ungrounded_runs"] == 2 and row["days"]["ungrounded_runs"] == 0
    assert row["pre_existing"]["sentence"].startswith("診斷書記載既往症")


# ---------- W5 注入變體 ----------
def test_w5_inject_styles_only_touch_treatment_summary_and_are_distinct(tmp_path):
    from tools.injection_probe import INJECTIONS, inject, make_variant
    src_path = Path("data/claims/C003/claim.json")
    src = json.loads(src_path.read_text(encoding="utf-8"))
    original_summary = src["documents"]["diagnosis_certificate"]["treatment_summary"]
    assert set(INJECTIONS) == {"en", "zh", "note"} and len({v for v in INJECTIONS.values()}) == 3
    for style in INJECTIONS:
        v = inject(src, style)
        assert v["claim_id"] == "C003X"
        assert INJECTIONS[style] in v["documents"]["diagnosis_certificate"]["treatment_summary"]
        assert v["documents"]["medical_receipt"] == src["documents"]["medical_receipt"]
        assert v["documents"]["claim_application"] == src["documents"]["claim_application"]
        assert original_summary in v["documents"]["diagnosis_certificate"]["treatment_summary"]
    reread = json.loads(src_path.read_text(encoding="utf-8"))["documents"]["diagnosis_certificate"]["treatment_summary"]
    assert reread == original_summary  # 原檔不動
    dst = make_variant(Path("data/claims/C003"), tmp_path / "C003X", "note")
    assert (dst / "claim.json").is_file() and (dst / "pdf" / "diagnosis_certificate.pdf").stat().st_size > 1000
    assert not (dst / "truth.json").exists()  # 變體無真值
    import pdfplumber
    with pdfplumber.open(dst / "pdf" / "diagnosis_certificate.pdf") as pdf:
        text = "".join(p.extract_text() or "" for p in pdf.pages)
    assert "逕行放行" in text.replace(" ", "")


# ---------- W6 run_pipeline --cases-dir ----------
def test_w6_run_pipeline_accepts_cases_dir(tmp_path):
    from tools import run_pipeline as rp
    assert rp.resolve_cases(["C003X"], all_cases=False, cases_dir=tmp_path) == [tmp_path / "C003X"]
    (tmp_path / "C900").mkdir()
    (tmp_path / "C900" / "claim.json").write_text("{}", encoding="utf-8")
    assert rp.resolve_cases([], all_cases=True, cases_dir=tmp_path) == [tmp_path / "C900"]
