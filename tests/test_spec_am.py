"""SPEC-am 覆核閉環(⑫ 方案 A)S1 程式端:卡片覆核連結、覆核紀錄驗證、閉環統計。

資料流:摘要信卡片 → Logic App ① 表單(GET 不寫)→ ② POST 寫 runs/reviews-inbox/<guid>.json(欄位原樣)
→ 桌機下載 → tools/review_stats.py。檔名由 Logic App guid() 產生,欄位合法性一律在此離線驗證。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.review import card as rc
from app.review.record import ReviewRecord, latest_by_case, load_reviews
from app.schemas.claim import ClaimBundle, ClaimDecision, GateFlag
from tests.test_pipeline import _bundle_payload
from tests.test_review import _c055, _output
from tools import review_stats as rs

ACTION = "https://prod.logic.azure.com/workflows/w/triggers/manual/paths/invoke?api-version=2016-10-01&sig=SIG"


# ── 卡片連結 ─────────────────────────────
def test_am1_no_action_url_card_bytes_unchanged():
    out, bundle = _c055()
    assert rc.build_review_card(out, bundle).html == rc.build_review_card(out, bundle, action_url=None).html
    assert "填寫覆核" not in rc.build_review_card(out, bundle).html


def test_am1_link_carries_run_and_case_and_keeps_signature():
    q = parse_qs(urlsplit(rc.review_link(ACTION, "20261010T0514-16157b3", "C055")).query)
    assert q["run"] == ["20261010T0514-16157b3"] and q["case"] == ["C055"]
    assert q["sig"] == ["SIG"] and q["api-version"] == ["2016-10-01"]


def test_am1_card_with_action_url_has_escaped_link():
    out, bundle = _c055()
    html = rc.build_review_card(out, bundle, action_url=ACTION, run_tag="run9").html
    assert "填寫覆核" in html and "run=run9" in html and "case=C055" in html
    assert "&amp;sig=SIG" in html  # href 內 & 經 html escape


def test_am1_hostile_run_tag_never_raw_in_html():
    out, bundle = _c055()
    evil = '"><script>alert(1)</script>'
    html = rc.build_review_card(out, bundle, action_url=ACTION, run_tag=evil).html
    assert "<script>" not in html and evil not in html


def test_am1_digest_collector_passes_action_url():
    out, bundle = _c055()
    col = rc.DigestCollector("run9", action_url=ACTION)
    col(out, bundle)
    assert "case=C055" in col.cards[0].html and "run=run9" in col.cards[0].html


# ── 覆核紀錄 ─────────────────────────────
def _rec(case: str = "C055", decision: str = "approve", amount: str = "", at: str = "2026-10-11T01:00:00Z",
         run: str = "run9", ua: str = "Mozilla/5.0", note: str = "") -> dict[str, Any]:
    return {"run_id": run, "claim_id": case, "decision": decision, "amount": amount, "note": note,
            "submitted_at": at, "user_agent": ua}


def _write(d: Path, recs: list[dict[str, Any]], raw: list[str] | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(recs):
        (d / f"{i:04d}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    for j, t in enumerate(raw or []):
        (d / f"raw{j}.json").write_text(t, encoding="utf-8")
    return d


def test_am2_valid_record_parsed_amount_optional():
    r = ReviewRecord.model_validate(_rec(amount="12000"))
    assert r.decision is ClaimDecision.APPROVE and str(r.amount) == "12000"
    assert ReviewRecord.model_validate(_rec(amount="")).amount is None


@pytest.mark.parametrize("bad", [
    {"decision": "pay"}, {"claim_id": "C55"}, {"claim_id": "../x"}, {"run_id": "a/b"}, {"amount": "-1"},
    {"amount": "abc"}, {"submitted_at": "yesterday"}, {"note": "x" * 501},
])
def test_am2_invalid_records_rejected(bad):
    with pytest.raises(ValueError):
        ReviewRecord.model_validate({**_rec(), **bad})


def test_am2_load_counts_invalid_and_bad_json(tmp_path):
    d = _write(tmp_path / "r", [_rec(), _rec(decision="pay")], raw=["{not json"])
    recs, invalid = load_reviews(d)
    assert len(recs) == 1 and invalid == 2


def test_am2_latest_wins_and_counts_superseded():
    recs = [ReviewRecord.model_validate(_rec(decision="deny", at="2026-10-11T01:00:00Z")),
            ReviewRecord.model_validate(_rec(decision="approve", at="2026-10-11T02:00:00Z")),
            ReviewRecord.model_validate(_rec(case="C001", run="other"))]
    latest, superseded = latest_by_case(recs, "run9")
    assert set(latest) == {"C055"} and latest["C055"].decision is ClaimDecision.APPROVE and superseded == 1


@given(st.permutations(list(range(4))))
def test_am2_property_latest_independent_of_order(order):
    base = [ReviewRecord.model_validate(_rec(decision=d, at=f"2026-10-11T0{i}:00:00Z"))
            for i, d in enumerate(["deny", "manual_review", "approve", "deny"])]
    latest, superseded = latest_by_case([base[i] for i in order], "run9")
    assert latest["C055"].decision is ClaimDecision.DENY
    assert str(latest["C055"].submitted_at).startswith("2026-10-11 03")
    assert superseded == 3


# ── 閉環統計 ─────────────────────────────
TRUTHS = {"C055": {"decision": "approve", "approved_amount": "82000"},
          "C001": {"decision": "approve", "approved_amount": "18000"},
          "C031": {"decision": "manual_review", "approved_amount": "0"}}


def _pred(tmp_path: Path) -> Path:
    d = tmp_path / "pred"
    d.mkdir()
    out, _ = _c055()  # 原判 approve、最終 manual
    (d / "C055.json").write_text(out.model_dump_json(), encoding="utf-8")
    flagged = _output("C031", "POL-B", "第7條", flags=[GateFlag(check="x", detail="y")])  # 原判 approve、最終 manual
    (d / "C031.json").write_text(flagged.model_dump_json(), encoding="utf-8")
    auto = _output("C001", "POL-A", "第7條")  # 自動核付:不在卡片內
    (d / "C001.json").write_text(auto.model_dump_json(), encoding="utf-8")
    return d


def test_am3_stats_hand_computed(tmp_path):
    reviews = _write(tmp_path / "r", [
        _rec("C055", "approve", "82000", "2026-10-11T01:00:00Z"),   # 與真值一致、與原判一致
        _rec("C031", "deny", "", "2026-10-11T01:10:00Z"),          # 推翻原判 approve、與真值 manual 不一致
        _rec("C031", "approve", "", "2026-10-11T01:05:00Z"),       # 較早 → 被覆蓋
        _rec("C055", "pay"),                                       # 無效
    ])
    r = rs.compute(reviews, _pred(tmp_path), "run9", TRUTHS)
    assert r["cards"] == 2 and r["reviewed"] == 2 and r["coverage"] == "1.000"
    assert r["overrides"] == ["C031"] and r["disagree_with_truth"] == ["C031"]
    assert r["superseded"] == 1 and r["invalid"] == 1 and r["suspicious"] == []


def test_am3_amount_mismatch_counts_as_truth_disagreement(tmp_path):
    reviews = _write(tmp_path / "r", [_rec("C055", "approve", "80000")])
    r = rs.compute(reviews, _pred(tmp_path), "run9", TRUTHS)
    assert r["disagree_with_truth"] == ["C055"] and r["overrides"] == []


def test_am3_burst_submissions_flagged_suspicious(tmp_path):
    """pre-mortem 徵兆:同輪多件在 60 秒內、間隔 < 5 秒 → 疑似掃描器代送。"""
    reviews = _write(tmp_path / "r", [_rec("C055", at="2026-10-11T01:00:00Z"),
                                      _rec("C031", at="2026-10-11T01:00:02Z"),
                                      _rec("C001", at="2026-10-11T01:00:04Z")])
    r = rs.compute(reviews, _pred(tmp_path), "run9", TRUTHS)
    assert sorted(r["suspicious"]) == ["C001", "C031", "C055"]


def test_am3_scanner_user_agent_flagged(tmp_path):
    reviews = _write(tmp_path / "r", [_rec("C055", ua="Microsoft Office SafeLinks/1.0")])
    assert rs.compute(reviews, _pred(tmp_path), "run9", TRUTHS)["suspicious"] == ["C055"]


def test_am3_cli_writes_report(tmp_path, capsys):
    reviews = _write(tmp_path / "r", [_rec("C055", "approve", "82000")])
    out = tmp_path / "report.json"
    assert rs.main(["--reviews", str(reviews), "--pred", str(_pred(tmp_path)), "--run", "run9",
                    "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["reviewed"] == 1
    assert "覆核" in capsys.readouterr().out


def test_am3_bundle_unused_names_not_in_report(tmp_path):
    """報表只含案號與決定,不含姓名(固定解法 21:note 為自由文字,亦不輸出)。"""
    name = ClaimBundle.model_validate(_bundle_payload("C055")).diagnosis_certificate.patient_name.value
    reviews = _write(tmp_path / "r", [_rec("C055", note=f"{name} 已確認")])
    r = rs.compute(reviews, _pred(tmp_path), "run9", TRUTHS)
    assert name not in json.dumps(r, ensure_ascii=False)


def test_am1_run_tag_with_ampersand_round_trips():
    """未做 URL 編碼時 HTML 跳脫仍擋得住 XSS,但 & 會把查詢參數切壞 → run 須完整還原、不得多出參數。"""
    q = parse_qs(urlsplit(rc.review_link(ACTION, "a&x=1", "C055")).query)
    assert q["run"] == ["a&x=1"] and "x" not in q
