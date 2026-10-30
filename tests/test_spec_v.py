"""SPEC-v:Content Safety 接入(方案 A 影子模式)——groundedness(reasoning + relevance)+ 四類掃描,
結果記 PipelineOutput.safety,不改 final_decision;送出文字先遮罩;任何例外不中斷管線。"""
from __future__ import annotations

import json
import urllib.error
from decimal import Decimal
from typing import Any

import pytest

from app.pipeline import cs_client
from app.schemas.claim import PipelineOutput
from tests.test_entra_auth import _Resp


def _capture(monkeypatch, responses: list[Any]) -> list[Any]:
    seen: list[Any] = []

    def fake_urlopen(req, timeout=0):
        seen.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(cs_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cs_client.time, "sleep", lambda s: None)
    return seen


def _http_error(code: int) -> urllib.error.HTTPError:
    import io
    return urllib.error.HTTPError("https://cs", code, "err", {}, io.BytesIO(b'{"error":"x"}'))  # type: ignore[arg-type]


GROUNDED = {"ungroundedDetected": False, "ungroundedPercentage": 0, "ungroundedDetails": []}
UNGROUNDED = {"ungroundedDetected": True, "ungroundedPercentage": 0.4,
              "ungroundedDetails": [{"text": "<句子甲>", "offset": {"utf16": 0}, "length": {"utf16": 4}}]}
HARM = {"categoriesAnalysis": [{"category": "Hate", "severity": 0}, {"category": "SelfHarm", "severity": 0},
                               {"category": "Sexual", "severity": 0}, {"category": "Violence", "severity": 2}]}


# ---------- V1 client ----------
def test_v1_1_requires_key_or_token_and_picks_header(monkeypatch):
    with pytest.raises(ValueError):
        cs_client.CSClient("https://cs.x", key="")
    seen = _capture(monkeypatch, [_Resp(GROUNDED), _Resp(GROUNDED)])
    cs_client.CSClient("https://cs.x/", key="k").detect_groundedness("t", ["s"])
    cs_client.CSClient("https://cs.x/", token_provider=lambda: "jwt-s3cr3t").detect_groundedness("t", ["s"])
    assert seen[0].get_header("Ocp-apim-subscription-key") == "k" and seen[0].get_header("Authorization") is None
    assert seen[1].get_header("Authorization") == "Bearer jwt-s3cr3t"
    assert seen[1].get_header("Ocp-apim-subscription-key") is None


def test_v1_2_groundedness_request_shape_and_result(monkeypatch):
    seen = _capture(monkeypatch, [_Resp(UNGROUNDED)])
    r = cs_client.CSClient("https://cs.x", key="k").detect_groundedness("<理由>", ["<源頭一>", "<源頭二>"])
    assert seen[0].full_url == f"https://cs.x/contentsafety/text:detectGroundedness?api-version={cs_client.GROUNDEDNESS_API}"
    body = json.loads(seen[0].data)
    assert body == {"domain": "Medical", "task": "Summarization", "text": "<理由>",
                    "groundingSources": ["<源頭一>", "<源頭二>"], "reasoning": False}
    assert r.ungrounded is True and r.percentage == Decimal("0.4")
    assert r.details == ["<句子甲>"] and r.truncated is False


def test_v1_3_analyze_text_returns_four_severities(monkeypatch):
    seen = _capture(monkeypatch, [_Resp(HARM)])
    h = cs_client.CSClient("https://cs.x", key="k").analyze_text("<文字>")
    assert seen[0].full_url.endswith(f"/contentsafety/text:analyze?api-version={cs_client.ANALYZE_API}")
    assert json.loads(seen[0].data)["text"] == "<文字>"
    assert h == {"Hate": 0, "SelfHarm": 0, "Sexual": 0, "Violence": 2}


def test_v1_4_retries_429_and_5xx_then_raises_cserror_on_4xx(monkeypatch):
    seen = _capture(monkeypatch, [_http_error(429), _http_error(503), _Resp(GROUNDED)])
    r = cs_client.CSClient("https://cs.x", key="k").detect_groundedness("t", ["s"])
    assert r.ungrounded is False and len(seen) == 3
    _capture(monkeypatch, [_http_error(429)] * 3)
    with pytest.raises(cs_client.CSError):
        cs_client.CSClient("https://cs.x", key="k").detect_groundedness("t", ["s"])
    _capture(monkeypatch, [_http_error(400)])
    with pytest.raises(cs_client.CSError):
        cs_client.CSClient("https://cs.x", key="k").detect_groundedness("t", ["s"])


def test_v1_5_clips_text_and_drops_longest_sources_until_within_limits():
    text, sources, truncated = cs_client.clip_inputs("x" * 8000, ["a" * 30000, "b" * 20000, "c" * 10000])
    assert len(text) == cs_client.TEXT_MAX_CHARS and truncated is True
    assert sources == ["b" * 20000, "c" * 10000]  # 丟最長的 a,合計 30,000 ≤ 55,000;順序保留
    t2, s2, tr2 = cs_client.clip_inputs("short", ["s"])
    assert (t2, s2, tr2) == ("short", ["s"], False)


def test_v1_5b_groundedness_marks_truncated_when_clipped(monkeypatch):
    seen = _capture(monkeypatch, [_Resp(GROUNDED)])
    r = cs_client.CSClient("https://cs.x", key="k").detect_groundedness("x" * 8000, ["s"])
    assert r.truncated is True and len(json.loads(seen[0].data)["text"]) == cs_client.TEXT_MAX_CHARS


# ---------- V2 schema ----------
def test_v2_pipeline_output_safety_optional_and_absent_from_dump_when_none():
    from app.schemas.claim import GroundednessResult, SafetyRecord
    from tests.test_review import _output
    out = _output("C001", "POL-A", "第7條")
    assert out.safety is None  # 落地檔不含 safety 鍵由 runner 的 exclude 保證(V3-6 真實寫檔驗)
    rec = SafetyRecord(invoked=True, groundedness=GroundednessResult(ungrounded=True, percentage=Decimal("0.4"),
                                                                       details=["<句子甲>"], truncated=False),
                       harm={"Hate": 0, "SelfHarm": 0, "Sexual": 0, "Violence": 0}, error="", elapsed_ms=120)
    out2 = out.model_copy(update={"safety": rec})
    d = json.loads(out2.model_dump_json())
    assert d["safety"]["groundedness"]["percentage"] == "0.4" and d["safety"]["harm"]["Violence"] == 0
    assert PipelineOutput.model_validate(d).safety == rec  # 往返
    assert out2.gate == out.gate and out2.adjudication == out.adjudication  # 影子欄位不碰判定


# ---------- V3 safety 流程(runner / 工具 / eval / 卡片)----------
from app.gate.policy_index import load_policy_index  # noqa: E402
from app.schemas.claim import GroundednessResult, SafetyRecord  # noqa: E402
from tests.test_review import _c055, _output  # noqa: E402

INDEX = load_policy_index()


class _FakeCS:
    def __init__(self, pct: str = "0.4", fail: bool = False):
        self.pct, self.fail, self.calls = pct, fail, []  # type: ignore[var-annotated]

    def detect_groundedness(self, text, sources, domain="Medical"):
        self.calls.append(("g", text, sources, domain))
        if self.fail:
            raise cs_client.CSError("HTTP 500")
        return GroundednessResult(ungrounded=Decimal(self.pct) > 0, percentage=Decimal(self.pct), details=[],
                                  truncated=False)

    def analyze_text(self, text):
        self.calls.append(("a", text))
        return {"Hate": 0, "SelfHarm": 0, "Sexual": 0, "Violence": 0}


def test_v3_1_inputs_are_redacted_reasoning_plus_relevance_and_sources_include_bundle_and_cited_clauses():
    from app.pipeline import safety
    out, bundle = _c055()
    names = {bundle.diagnosis_certificate.patient_name.value, bundle.claim_application.applicant_name.value}
    leaky = out.adjudication.model_copy(update={
        "reasoning": f"{bundle.diagnosis_certificate.patient_name.value} 於本院住院,依第8條核付",
        "citations": [c.model_copy(update={"relevance": f"{bundle.claim_application.applicant_name.value} 申請病房費"})
                      for c in out.adjudication.citations]})
    out = out.model_copy(update={"adjudication": leaky})
    text, sources = safety.build_inputs(out, bundle, INDEX)
    assert "於本院住院,依第8條核付" in text and "申請病房費" in text and text.count("<被保險人>") == 2
    assert all(n not in text and all(n not in s for s in sources) for n in names)
    assert bundle.diagnosis_certificate.treatment_summary.value in sources[0]
    assert bundle.diagnosis_certificate.diagnosis.value in sources[0]
    assert all(it.item_name in sources[1] for it in bundle.medical_receipt.items)
    clause = INDEX["POL-C"]["第8條"]  # 引用條款逐條入 sources(V3-12:帶標籤、數字旁註)
    assert any(s_.startswith(f"POL-C 第8條({clause.title}):") and clause.text[:12] in s_ for s_ in sources)
    assert len(sources) == 4  # 診斷書 / 收據 / 推導事實(V3-9)/ 引用條款


def test_v3_2_safety_check_records_result_and_never_raises():
    from app.pipeline import safety
    out, bundle = _c055()
    ok = safety.safety_check(out, bundle, _FakeCS("0.4"), INDEX)
    assert ok.invoked and ok.groundedness is not None and ok.groundedness.percentage == Decimal("0.4")
    assert ok.harm == {"Hate": 0, "SelfHarm": 0, "Sexual": 0, "Violence": 0} and ok.error == ""
    bad = safety.safety_check(out, bundle, _FakeCS(fail=True), INDEX)
    assert bad.invoked and bad.groundedness is None and "CSError" in bad.error and bad.harm is not None

    class Boom:
        def detect_groundedness(self, *a, **k):
            raise RuntimeError("x sig=SECRET")

        def analyze_text(self, *a, **k):
            raise RuntimeError("y")
    worst = safety.safety_check(out, bundle, Boom(), INDEX)
    assert worst.groundedness is None and worst.harm is None
    assert "RuntimeError" in worst.error and "SECRET" not in worst.error


def test_v3_2b_final_decision_and_gate_untouched_by_safety():
    from app.pipeline import safety
    out, bundle = _c055()
    after = safety.attach_safety(out, bundle, _FakeCS("0.9"), INDEX)
    assert after.gate == out.gate and after.adjudication == out.adjudication and after.safety is not None
    assert after.safety.groundedness is not None and after.safety.groundedness.ungrounded is True


def test_v3_6_output_json_omits_safety_and_repair_when_none(tmp_path):
    from app.pipeline import runner
    out, bundle = _c055()
    plain = json.loads(runner.output_json(out))
    assert "safety" not in plain and "repair" not in plain
    with_safety = out.model_copy(update={"safety": SafetyRecord(invoked=True)})
    d = json.loads(runner.output_json(with_safety))
    assert "safety" in d and "repair" not in d


def test_v3_3_resafety_tool_writes_sidecar_and_skips_existing(tmp_path):
    from app.pipeline import runner
    from tools import resafety
    out, bundle = _c055()
    pred = tmp_path / "pred"
    pred.mkdir()
    for cid in ("C055", "C056"):
        o = out.model_copy(update={"claim_id": cid})
        (pred / f"{cid}.json").write_text(runner.output_json(o), encoding="utf-8")
        (pred / f"{cid}.bundle.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    no_bundle = out.model_copy(update={"claim_id": "C057"})  # 無 bundle → 跳過
    (pred / "C057.json").write_text(runner.output_json(no_bundle), encoding="utf-8")
    fake = _FakeCS("0.2")
    assert resafety.main([str(pred), "--limit", "1"], client=fake) == 0
    assert (pred / "C055.safety.json").exists() and not (pred / "C056.safety.json").exists()
    assert resafety.main([str(pred)], client=fake) == 0
    assert (pred / "C056.safety.json").exists() and not (pred / "C057.safety.json").exists()
    assert len(fake.calls) == 4  # 2 案 × (groundedness + analyze);C055 第二次已存在不重打
    rec = SafetyRecord.model_validate_json((pred / "C055.safety.json").read_text(encoding="utf-8"))
    assert rec.groundedness is not None and rec.groundedness.percentage == Decimal("0.2")


def test_v3_4_eval_reports_groundedness_distribution_by_group_only_when_present():
    from eval.run_eval import evaluate, load_truths
    truths = load_truths()
    base = {cid: _output(cid, "POL-A", "第7條") for cid in list(truths)[:3]}
    assert "groundedness" not in evaluate(truths, base, INDEX)
    cids = list(base)
    enriched = {
        cids[0]: base[cids[0]].model_copy(update={"safety": SafetyRecord(invoked=True, groundedness=GroundednessResult(
            ungrounded=False, percentage=Decimal("0.1"), details=[]))}),
        cids[1]: base[cids[1]].model_copy(update={"safety": SafetyRecord(invoked=True, groundedness=GroundednessResult(
            ungrounded=True, percentage=Decimal("0.5"), details=["x"]))}),
        cids[2]: base[cids[2]],
    }
    g = evaluate(truths, enriched, INDEX)["groundedness"]
    assert g["n_with_result"] == 2 and g["n_missing"] == 1
    grp = g["by_final_decision"]["approve"]
    assert grp["n"] == 2 and grp["median"] == "0.3" and grp["p90"] == "0.5" and grp["ungrounded"] == 1


def test_v3_5_card_and_digest_show_ungrounded_percentage_or_dash():
    from app.review import card as rc
    out, bundle = _c055()
    plain = rc.build_review_card(out, bundle)
    assert plain.ungrounded_pct is None and "ungrounded" in rc.card_body(plain) and "—" in rc.card_body(plain)
    with_s = out.model_copy(update={"safety": SafetyRecord(invoked=True, groundedness=GroundednessResult(
        ungrounded=True, percentage=Decimal("0.4"), details=["句子甲"]))})
    c2 = rc.build_review_card(with_s, bundle)
    assert c2.ungrounded_pct == "40%" and "40%" in rc.card_body(c2) and "句子甲" in rc.card_body(c2)
    digest = rc.build_digest([plain, c2], "t")
    assert "<th>ungrounded</th>" in digest.html and "40%" in digest.html


def test_v3_7_make_cs_client_picks_mode(monkeypatch):
    from app.pipeline import auth
    monkeypatch.setenv(auth.AUTH_ENV, "key")
    monkeypatch.setenv("CS_KEY", "k")
    c = auth.make_cs_client("https://cs.x")
    assert isinstance(c, cs_client.CSClient) and c._key == "k" and c._token_provider is None
    monkeypatch.setenv(auth.AUTH_ENV, "entra")
    monkeypatch.setattr(auth, "entra_token_provider", lambda cred=None: (lambda: "t"))
    c2 = auth.make_cs_client("https://cs.x")
    assert c2._token_provider is not None and c2._key == ""


def test_v3_4b_load_predictions_merges_safety_sidecar(tmp_path):
    from app.pipeline import runner
    from eval.run_eval import load_predictions
    out, _ = _c055()
    (tmp_path / "C055.json").write_text(runner.output_json(out), encoding="utf-8")
    g = GroundednessResult(ungrounded=False, percentage=Decimal("0.1"), details=[])
    rec = SafetyRecord(invoked=True, groundedness=g)
    (tmp_path / "C055.safety.json").write_text(rec.model_dump_json(), encoding="utf-8")
    other = out.model_copy(update={"claim_id": "C056"})
    (tmp_path / "C056.json").write_text(runner.output_json(other), encoding="utf-8")
    preds = load_predictions(tmp_path, ["C055", "C056"])
    assert preds["C055"].safety == rec and preds["C056"].safety is None
    assert preds["C055"].gate == out.gate  # 合併不碰判定


def test_v3_8_text_sentence_boundaries_normalized_to_ascii_period():
    """桌機 2026-10-05 實測:detectGroundedness 只認 ASCII '.' 為句界,中文「。」與換行會把整段當一塊(判定不穩定)。"""
    from app.pipeline import safety
    assert safety.segment_sentences("甲句。乙句!丙句?丁句;戊句\n己句。") == "甲句.\n乙句.\n丙句.\n丁句.\n戊句.\n己句."
    assert safety.segment_sentences("已有句點.\n另一句") == "已有句點.\n另一句."
    assert safety.segment_sentences("  ") == ""
    out, bundle = _c055()
    adj = out.adjudication.model_copy(update={"reasoning": "第一句。第二句!", "citations": [
        c.model_copy(update={"relevance": "適用性說明;補充"}) for c in out.adjudication.citations]})
    text, _ = safety.build_inputs(out.model_copy(update={"adjudication": adj}), bundle, INDEX)
    assert "。" not in text and "!" not in text and ";" not in text
    assert text.startswith("第一句.\n第二句.\n") and text.endswith("適用性說明.\n補充.")


def test_v3_9_sources_include_derived_facts_and_application_dates_but_not_calculation():
    """v9 全量(2026-10-05):放行案 ungrounded 中位數 0.58,被判無據的多為「住院 6 日」「距生效 218 日」等程式推導事實
    ——它們來自 derived_facts / 申請書欄位卻不在 sources。補入;calculation 行不入(避免模型數字自我背書)。"""
    from app.pipeline import safety
    from app.pipeline.derived import derive_facts
    out, bundle = _c055()
    facts = derive_facts(bundle)
    _, sources = safety.build_inputs(out, bundle, INDEX)
    joined = "\n".join(sources)
    app_ = bundle.claim_application
    assert app_.policy_effective_date.value in joined and app_.application_date.value in joined
    assert app_.policy_number.value in joined
    assert f"{facts.days_since_effective}" in joined and f"{facts.total_days}" in joined
    for a in facts.admissions:
        assert a.admission_date in joined and a.discharge_date in joined and f"{a.days_inclusive}" in joined
    assert f"{facts.stay_group_days[0]}" in joined and f"{facts.days_event_to_application}" in joined
    assert not any("<項目>" in s for s in sources)  # calculation 的 item 名不入 sources
    assert len(sources) == 4  # 診斷書 / 收據 / 推導事實 / 引用條款(C055 一條)


def test_v3_10_reasoning_only_mode_drops_relevance_from_text():
    from app.pipeline import safety
    out, bundle = _c055()
    full, _ = safety.build_inputs(out, bundle, INDEX)
    only, _ = safety.build_inputs(out, bundle, INDEX, reasoning_only=True)
    assert only == safety.segment_sentences(out.adjudication.reasoning) and len(only) < len(full)
    rec = safety.safety_check(out, bundle, _FakeCS("0.1"), INDEX, reasoning_only=True)
    assert rec.groundedness is not None  # 無例外


# ---------- V3-11~14 sources 正規化(2026-10-05 盤點 269 無據句:98 含條號、45 阿拉伯數字、110 英文)----------
def test_v3_11_annotate_numerals_adds_arabic_after_chinese_amounts_and_days():
    from app.pipeline.safety import annotate_numerals as an
    assert an("每日新臺幣貳仟元") == "每日新臺幣貳仟元(2000元)"
    assert an("最高新臺幣貳萬伍仟元") == "最高新臺幣貳萬伍仟元(25000元)"
    assert an("壹拾萬元之限額") == "壹拾萬元(100000元)之限額"
    assert an("以九十日為限;三十日等待期") == "以九十日(90日)為限;三十日(30日)等待期"
    assert an("出院後十四日內") == "出院後十四日(14日)內"
    assert an("同一次住院") == "同一次住院"  # 「次」不旁註
    assert an("已有 2000元") == "已有 2000元"  # 阿拉伯數字不重複旁註


def test_v3_12_clause_sources_carry_policy_and_clause_label():
    from app.pipeline import safety
    out, bundle = _c055()
    _, sources = safety.build_inputs(out, bundle, INDEX)
    clause = INDEX["POL-C"]["第8條"]
    labelled = [s for s in sources if s.startswith(f"POL-C 第8條({clause.title}):")]
    assert len(labelled) == 1 and "參仟元(3000元)" in labelled[0]


def test_v3_13_derived_source_annotates_days_in_chinese():
    from app.pipeline import safety
    out, bundle = _c055()
    _, sources = safety.build_inputs(out, bundle, INDEX)
    derived = sources[2]
    assert "9 日(九日)" in derived and "5 日(五日)" in derived and "4 日(四日)" in derived
    from app.pipeline.safety import int_to_chinese as ic
    assert (ic(6), ic(10), ic(14), ic(20), ic(90), ic(97)) == ("六", "十", "十四", "二十", "九十", "九十七")


def test_v3_14_eval_groundedness_split_by_reasoning_language():
    from eval.run_eval import evaluate, load_truths, reasoning_language
    assert reasoning_language("本案符合給付條件,依第7條核付。") == "zh"
    assert reasoning_language("The claim satisfies all required aspects per clause 7.") == "en"
    assert reasoning_language("Intake: complete. 依 POL-A 第22條 等待期成立,距生效 617 日。") == "zh"
    truths = load_truths()
    cids = list(truths)[:2]
    g_lo = GroundednessResult(ungrounded=False, percentage=Decimal("0.1"), details=[])
    g_hi = GroundednessResult(ungrounded=True, percentage=Decimal("0.9"), details=["x"])
    base_zh = _output(cids[0], "POL-A", "第7條")
    zh_adj = base_zh.adjudication.model_copy(update={"reasoning": "本案符合第7條給付條件,核付住院日額。"})
    zh = base_zh.model_copy(update={"adjudication": zh_adj, "safety": SafetyRecord(invoked=True, groundedness=g_lo)})
    base_en = _output(cids[1], "POL-A", "第7條")
    en_adj = base_en.adjudication.model_copy(update={"reasoning": "The claim is approved under clause 7."})
    en = base_en.model_copy(update={"adjudication": en_adj, "safety": SafetyRecord(invoked=True, groundedness=g_hi)})
    g = evaluate(truths, {cids[0]: zh, cids[1]: en}, INDEX)["groundedness"]
    assert g["by_language"]["zh"]["approve"] == {"n": 1, "median": "0.1"}
    assert g["by_language"]["en"]["approve"] == {"n": 1, "median": "0.9"}
