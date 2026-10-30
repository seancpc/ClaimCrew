"""SPEC-r:Logic Apps 人工覆核卡片——確定性渲染、逃逸、送卡範圍、POST 容錯與秘密遮罩、離線工具、管線整合。"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.gate.policy_index import load_policy_index
from app.pipeline.runner import finalize_adjudication
from app.review import card as rc
from app.schemas.claim import (
    AdjudicationDraft,
    ClaimBundle,
    ClaimDecision,
    GateFlag,
    GateResult,
    PipelineOutput,
    RepairRecord,
)
from tests.test_pipeline import _bundle_payload, _decision_payload
from tests.test_spec_p import _capped_per_unit

INDEX = load_policy_index()


def _output(cid: str, policy: str, clause: str, *, decision: str = "approve", flags: list[GateFlag] | None = None,
            final: ClaimDecision | None = None, calculation: list[dict[str, Any]] | None = None,
            repair: RepairRecord | None = None) -> PipelineOutput:
    d = _decision_payload(cid, policy, clause)["adjudication"]
    d["decision"] = decision
    if calculation is not None:
        d["calculation"] = calculation
    adj = finalize_adjudication(AdjudicationDraft.model_validate(d))
    flags = flags or []
    final = final or (ClaimDecision.MANUAL_REVIEW if flags else adj.decision)
    gate = GateResult(claim_id=cid, original_decision=adj.decision, final_decision=final, flags=flags,
                      passed=not flags)
    return PipelineOutput(claim_id=cid, adjudication=adj, gate=gate, repair=repair)


def _c055() -> tuple[PipelineOutput, ClaimBundle]:
    bundle = ClaimBundle.model_validate(_bundle_payload("C055"))
    out = _output("C055", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 9, 3000, 27000)],
                  flags=[GateFlag(check="calc_segment_cap_mismatch", detail="Σ min(...) = 22000,明細算得 27000")])
    return out, bundle


# ---------- R1 確定性卡片、無病患姓名 ----------
def test_r1_card_is_deterministic_and_carries_decision_flags_and_facts():
    out, bundle = _c055()
    a, b = rc.build_review_card(out, bundle), rc.build_review_card(out, bundle)
    assert a.html == b.html and a.model_dump() == b.model_dump()
    assert a.claim_id == "C055" and a.final_decision is ClaimDecision.MANUAL_REVIEW
    assert a.original_decision is ClaimDecision.APPROVE and a.approved_amount == Decimal(27000)
    assert [f.check for f in a.flags] == ["calc_segment_cap_mismatch"]
    assert a.facts["policy_number"] == bundle.claim_application.policy_number.value
    assert a.facts["hospital"] == bundle.diagnosis_certificate.hospital_name.value
    assert "C055" in a.subject and "manual_review" in a.subject
    for s in ("calc_segment_cap_mismatch", "22000", "27000", "第8條", bundle.claim_application.policy_number.value):
        assert s in a.html
    assert a.advisory is None and "無顧問建議" in a.html


def test_r1_patient_and_applicant_names_never_enter_card():
    out, bundle = _c055()
    pn = bundle.diagnosis_certificate.patient_name.value
    an = bundle.claim_application.applicant_name.value
    assert pn and an
    c = rc.build_review_card(out, bundle)
    dumped = c.model_dump_json()
    assert pn not in c.html and an not in c.html and pn not in dumped and an not in dumped


def test_r1_advisory_section_from_repair_record():
    out, bundle = _c055()
    rep = RepairRecord(invoked=True, plan="repairable", rounds=1, flags_before=out.gate.flags, flags_after=[],
                       guard_reason="released", pre_repair_decision=ClaimDecision.APPROVE,
                       pre_repair_amount=Decimal(27000), repaired_decision=ClaimDecision.APPROVE,
                       repaired_amount=Decimal(22000), second_opinion_decision=ClaimDecision.APPROVE,
                       second_opinion_amount=Decimal(22000), would_release=True)
    c = rc.build_review_card(out.model_copy(update={"repair": rep}), bundle)
    assert c.advisory is not None and c.advisory.would_release is True and c.advisory.repaired_amount == Decimal(22000)
    assert "22000" in c.html and "would_release" in c.html or "守門全過" in c.html
    blocked = rep.model_copy(update={"guard_reason": "facts_shrunk", "would_release": False})
    c2 = rc.build_review_card(out.model_copy(update={"repair": blocked}), bundle)
    assert c2.advisory is not None and c2.advisory.would_release is False and "facts_shrunk" in c2.html


def test_r1_names_inside_llm_free_text_are_redacted():
    """v8 C019/C020/C054 實證:LLM 的 relevance / reasoning 會寫出病患姓名 → 卡片層以已知姓名值遮罩。"""
    out, bundle = _c055()
    pn = bundle.diagnosis_certificate.patient_name.value
    cit = out.adjudication.citations[0].model_copy(update={"relevance": f"依據{pn}的住院事實"})
    adj = out.adjudication.model_copy(update={"reasoning": f"{pn}於住院期間接受治療", "citations": [cit]})
    c = rc.build_review_card(out.model_copy(update={"adjudication": adj}), bundle)
    assert pn not in c.model_dump_json() and pn not in c.html
    assert c.reasoning == "<被保險人>於住院期間接受治療" and c.citations[0].relevance == "依據<被保險人>的住院事實"
    assert "&lt;被保險人&gt;" in c.html


# ---------- R2 逃逸 ----------
def test_r2_hostile_text_is_escaped():
    out, bundle = _c055()
    evil = GateFlag(check="x", detail="<script>alert(1)</script>&\"'")
    out = out.model_copy(update={"gate": out.gate.model_copy(update={"flags": [evil]})})
    html = rc.build_review_card(out, bundle).html
    assert "<script" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&amp;" in html


# ---------- R3 送卡範圍 ----------
def test_r3_should_review_only_manual_unless_auto_deny_opted_in():
    out, _ = _c055()
    assert rc.should_review(out) is True
    approve = _output("C001", "POL-A", "第7條")
    assert rc.should_review(approve) is False
    auto_deny = _output("C004", "POL-A", "第7條", flags=[GateFlag(check="waiting_period_unmet", detail="x")],
                        final=ClaimDecision.DENY)
    assert rc.should_review(auto_deny) is False
    assert rc.should_review(auto_deny, include_auto_deny=True) is True
    model_deny = _output("C007", "POL-A", "第7條", decision="deny", calculation=[])
    assert rc.should_review(model_deny) is False and rc.should_review(model_deny, include_auto_deny=True) is False


# ---------- R4 POST 容錯與遮罩 ----------
def test_r4_mask_url_hides_sas_signature_only():
    u = "https://prod-00.eastus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?api-version=2016-10-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=SECRETVALUE123"
    m = rc.mask_url(u)
    assert "SECRETVALUE123" not in m and "sig=***" in m and "api-version=2016-10-01" in m and "workflows/abc" in m
    assert rc.mask_url("https://x/y") == "https://x/y"


class _Post:
    def __init__(self, status: int | Exception, headers: dict[str, str] | None = None, body: str = ""):
        self.status, self.headers, self.body, self.calls = status, headers or {}, body, []

    def __call__(self, url: str, body: bytes, timeout: float) -> tuple[int, dict[str, str], str]:
        self.calls.append((url, body, timeout))
        if isinstance(self.status, Exception):
            raise self.status
        return self.status, self.headers, self.body


def test_r4_post_card_records_outcome_and_never_raises():
    out, bundle = _c055()
    c = rc.build_review_card(out, bundle)
    url = "https://h/invoke?sig=TOPSECRET"
    ok = _Post(200, {"x-ms-workflow-run-id": "run-1"})
    r = rc.post_card(url, c, post=ok)
    assert r.status == "sent" and r.http_status == 200 and r.run_id == "run-1" and r.error == ""
    sent = json.loads(ok.calls[0][1])
    assert sent["claim_id"] == "C055" and sent["html"] == c.html and sent["subject"] == c.subject
    assert sent["final_decision"] == "manual_review" and sent["approved_amount"] == "27000"
    bad = rc.post_card(url, c, post=_Post(502, body='{"sent": false, "error": {"code": "Unauthenticated"}}'))
    assert bad.status == "error" and bad.http_status == 502
    assert "Unauthenticated" in bad.error  # 桌機 2026-10-03 實證:只印 HTTP 502 得翻執行歷程才知真因
    long = rc.post_card(url, c, post=_Post(502, body="x" * 1000))
    assert len(long.error) <= 320
    import urllib.error
    err = rc.post_card(url, c, post=_Post(urllib.error.URLError(f"boom {url}")))
    assert err.status == "error" and err.http_status is None and "TOPSECRET" not in err.error and "sig=***" in err.error


# ---------- R5 離線工具 ----------
def _pred_dir(tmp_path: Path, outs: list[tuple[PipelineOutput, ClaimBundle | None]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for o, b in outs:
        text = o.model_dump_json(exclude={"repair"} if o.repair is None else None)
        (tmp_path / f"{o.claim_id}.json").write_text(text, encoding="utf-8")
        if b is not None:
            (tmp_path / f"{o.claim_id}.bundle.json").write_text(b.model_dump_json(), encoding="utf-8")
    return tmp_path


def test_r5_send_review_dry_run_writes_html_without_network(tmp_path, monkeypatch, capsys):
    from tools import send_review as sr
    out, bundle = _c055()
    approve = _output("C001", "POL-A", "第7條")
    nobundle, _ = _c055()
    nobundle = nobundle.model_copy(update={"claim_id": "C099"})
    pred = _pred_dir(tmp_path / "pred", [(out, bundle), (approve, ClaimBundle.model_validate(_bundle_payload("C001"))),
                                         (nobundle, None)])
    monkeypatch.delenv("REVIEW_WEBHOOK_URL", raising=False)
    calls: list[Any] = []
    monkeypatch.setattr(sr, "post_card", lambda *a, **k: calls.append(a))
    dry = tmp_path / "html"
    assert sr.main([str(pred), "--per-case", "--dry-run-dir", str(dry)]) == 0  # SPEC-s 後預設摘要,逐案需 --per-case
    assert (dry / "C055.html").read_text(encoding="utf-8") == rc.build_review_card(out, bundle).html
    assert not (dry / "C001.html").exists() and calls == []
    text = capsys.readouterr().out
    assert "C099" in text and "C055" in text


def test_r5_send_review_requires_url_when_sending(tmp_path, monkeypatch):
    from tools import send_review as sr
    out, bundle = _c055()
    pred = _pred_dir(tmp_path, [(out, bundle)])
    monkeypatch.delenv("REVIEW_WEBHOOK_URL", raising=False)
    assert sr.main([str(pred)]) == 2
    monkeypatch.setenv("REVIEW_WEBHOOK_URL", "https://h/invoke?sig=S")
    posted: list[Any] = []
    monkeypatch.setattr(sr, "post_card", lambda url, c, **k: posted.append((url, c.claim_id)) or
                        rc.ReviewPost(status="sent", http_status=200, run_id="", error=""))
    assert sr.main([str(pred), "--per-case", "--limit", "1"]) == 0 and posted == [("https://h/invoke?sig=S", "C055")]


# ---------- R6 管線整合 ----------
def test_r6_run_case_review_hook_records_outcome_and_keeps_output_bytes(tmp_path):
    from app.pipeline import runner
    out, bundle = _c055()
    results: list[str] = []

    def reviewer(o: PipelineOutput, b: ClaimBundle) -> str:
        results.append(o.claim_id)
        return "sent"
    rec = runner.RunRecord(claim_id="C055", strategy="full", status="ok")
    runner._review_hook(rec, out, bundle, reviewer)
    assert rec.review == "sent" and results == ["C055"]
    rec2 = runner.RunRecord(claim_id="C055", strategy="full", status="ok")
    runner._review_hook(rec2, out, bundle, None)
    assert rec2.review == "off"
    approve = _output("C001", "POL-A", "第7條")
    rec3 = runner.RunRecord(claim_id="C001", strategy="full", status="ok")
    runner._review_hook(rec3, approve, bundle, reviewer)
    assert rec3.review == "skipped" and results == ["C055"]

    def boom(o: PipelineOutput, b: ClaimBundle) -> str:
        raise RuntimeError("network down sig=SECRET")
    rec4 = runner.RunRecord(claim_id="C055", strategy="full", status="ok")
    runner._review_hook(rec4, out, bundle, boom)
    assert rec4.review.startswith("error") and "SECRET" not in rec4.review and rec4.status == "ok"


def test_r6_review_field_defaults_off_so_old_run_logs_unchanged():
    from app.pipeline import runner
    rec = runner.RunRecord(claim_id="C001", strategy="full", status="ok")
    assert rec.review == "off"


# ====================== SPEC-s 摘要信(一輪一封) ======================
def _cards(n: int = 3) -> list[rc.ReviewCard]:
    out, bundle = _c055()
    cards = []
    for i in range(n):
        o = out.model_copy(update={"claim_id": f"C9{i:02d}"})
        cards.append(rc.build_review_card(o, bundle))
    return cards


def test_s1_digest_is_deterministic_with_toc_anchors_and_tag():
    cards = _cards(3)
    a, b = rc.build_digest(cards, "pred_full_v9"), rc.build_digest(cards, "pred_full_v9")
    assert a.html == b.html and a.claim_ids == ["C900", "C901", "C902"] and a.size_bytes == len(a.html.encode("utf-8"))
    assert "pred_full_v9" in a.subject and "3 案" in a.subject
    assert a.html.count('href="#C9') == 3 and all(f'id="{c}"' in a.html for c in a.claim_ids)
    assert a.html.count("<tr class=\"toc\">") == 3  # 目錄列數 == 卡片數
    assert "manual_review" in a.html and "27000" in a.html
    import re
    assert not re.search(r"20\d\d-\d\d-\d\d \d\d:\d\d", a.html)  # 無時間戳


def test_s2_split_digest_by_size_keeps_order_and_union():
    cards = _cards(3)
    one = rc.split_digest(cards, "t", max_bytes=10_000_000)
    assert len(one) == 1 and one[0].claim_ids == ["C900", "C901", "C902"] and "(1/1)" not in one[0].subject
    single = len(rc.build_digest(cards[:1], "t").html.encode("utf-8"))
    two = rc.split_digest(cards, "t", max_bytes=single * 2 + 200)  # 放得下 2 張、放不下 3 張
    assert [d.claim_ids for d in two] == [["C900", "C901"], ["C902"]]
    assert "(1/2)" in two[0].subject and "(2/2)" in two[1].subject
    each = rc.split_digest(cards, "t", max_bytes=1)  # 單卡也超限 → 仍獨立成封,不丟
    assert [d.claim_ids for d in each] == [["C900"], ["C901"], ["C902"]]


def test_s3_digest_keeps_name_redaction():
    out, bundle = _c055()
    pn = bundle.diagnosis_certificate.patient_name.value
    adj = out.adjudication.model_copy(update={"reasoning": f"{pn}於住院期間接受治療"})
    card = rc.build_review_card(out.model_copy(update={"adjudication": adj}), bundle)
    d = rc.build_digest([card], "t")
    assert pn not in d.html and "&lt;被保險人&gt;" in d.html


def test_s4_send_review_defaults_to_digest_and_writes_file_before_posting(tmp_path, monkeypatch):
    from tools import send_review as sr
    out, bundle = _c055()
    other = out.model_copy(update={"claim_id": "C077"})
    pred = _pred_dir(tmp_path / "pred_x", [(out, bundle), (other, bundle)])
    monkeypatch.setenv("REVIEW_WEBHOOK_URL", "https://h/invoke?sig=S")
    posted: list[Any] = []

    def fake_post(url: str, body: bytes, timeout: float) -> tuple[int, dict[str, str], str]:
        posted.append(json.loads(body))
        return 200, {"x-ms-workflow-run-id": "r1"}, "{}"
    monkeypatch.setattr(sr, "_poster", fake_post)
    assert sr.main([str(pred)]) == 0
    assert len(posted) == 1 and posted[0]["case_count"] == 2 and posted[0]["claim_ids"] == ["C055", "C077"]
    assert "pred_x" in posted[0]["subject"]
    assert posted[0]["html"] == (pred / "_review_digest.html").read_text(encoding="utf-8")
    # 失敗:檔案仍在、exit 1
    monkeypatch.setattr(sr, "_poster", lambda u, b, t: (401, {}, "nope"))
    (pred / "_review_digest.html").unlink()
    assert sr.main([str(pred)]) == 1 and (pred / "_review_digest.html").exists()
    # dry-run 不呼叫
    posted.clear()
    monkeypatch.setattr(sr, "_poster", fake_post)
    assert sr.main([str(pred), "--dry-run-dir", str(tmp_path / "d")]) == 0
    assert posted == [] and (tmp_path / "d" / "_review_digest.html").exists()
    # --per-case 回到逐案
    assert sr.main([str(pred), "--per-case"]) == 0 and [p["claim_id"] for p in posted] == ["C055", "C077"]


def test_s5_digest_collector_queues_only_review_cases_and_flushes_once(tmp_path):
    out, bundle = _c055()
    approve = _output("C001", "POL-A", "第7條")
    posted: list[Any] = []

    def fake_post(url: str, body: bytes, timeout: float) -> tuple[int, dict[str, str], str]:
        posted.append(json.loads(body))
        return 200, {}, "{}"
    col = rc.DigestCollector("run9", post=fake_post)
    assert col(out, bundle) == "queued" and col(approve, bundle) == "skipped" and len(col.cards) == 1
    results = col.flush("https://h/x?sig=S", tmp_path)
    assert [r.status for r in results] == ["sent"] and posted[0]["case_count"] == 1
    assert (tmp_path / "_review_digest.html").exists()
    assert col.flush("https://h/x?sig=S", tmp_path) == [] and len(posted) == 1  # 空集合不寄、不重寄
