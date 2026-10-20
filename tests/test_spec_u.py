"""SPEC-u(cloud1 首跑):A′ 第10條金額覆蓋 receipt_amount_uncovered(C036 藥費 12,200 漏行卻放行)、
B 既往症拒付源頭反查 pre_existing_unsupported(C066 復發案被引第19條拒付、真值 approve,歷次首件危險錯)、
run_tag 由 cloud_run 傳入(主旨「摘要 out」→ run_id)。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.schemas.claim import ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload, _decision_payload
from tests.test_spec_p import _adj, _capped, _capped_per_unit
from tests.test_spec_q import _items

INDEX = load_policy_index()
AMT = "receipt_amount_uncovered"
PRE = "pre_existing_unsupported"
ROOT = Path(__file__).resolve().parents[1]


def _flags(g, kind: str) -> list:
    return [f for f in g.flags if f.check == kind]


def _c036(items: list[tuple[str, str]]) -> ClaimBundle:
    return ClaimBundle.model_validate(_items(_bundle_payload("C036"), items))


C036_ITEMS = [("病房費", "9000"), ("藥費", "12200"), ("檢驗費", "6500")]


# ---------- U1 A′ 第10條金額覆蓋 ----------
def test_u1_1_flags_c036_shape_clause_10_actual_below_receipt_and_cap():
    bundle = _c036(C036_ITEMS)
    adj = _adj("C036", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000),
                                                      _capped("第10條", 6500, 60000).model_dump(mode="json")])
    g = checks.run_gate(adj, bundle, INDEX)
    fl = _flags(g, AMT)
    assert len(fl) == 1
    assert "18700" in fl[0].detail and "6500" in fl[0].detail and "60000" in fl[0].detail
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW
    assert _flags(g, "receipt_category_uncovered") == []  # 類別層級有行,SPEC-q 不會接


def test_u1_2_no_flag_when_actual_equals_receipt_sum():
    bundle = _c036(C036_ITEMS)
    adj = _adj("C036", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000),
                                                      _capped("第10條", 18700, 60000).model_dump(mode="json")])
    assert _flags(checks.run_gate(adj, bundle, INDEX), AMT) == []


def test_u1_3_no_flag_when_cap_binds():
    bundle = _c036([("病房費", "9000"), ("藥費", "80000")])
    adj = _adj("C036", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000),
                                                      _capped("第10條", 60000, 60000).model_dump(mode="json")])
    assert _flags(checks.run_gate(adj, bundle, INDEX), AMT) == []


def test_u1_4_compares_pre_ratio_actual_not_post_ratio_amount():
    bundle = _c036(C036_ITEMS)  # 收據 18,700;actual 18,700 × 0.65 = 12,155 < 18,700,不得因 ratio 誤旗標
    line = _capped("第10條", 18700, 60000, ratio="0.65").model_dump(mode="json")
    adj = _adj("C036", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000), line])
    assert _flags(checks.run_gate(adj, bundle, INDEX), AMT) == []


def test_u1_5_no_clause_10_line_is_left_to_category_check():
    bundle = _c036(C036_ITEMS)
    adj = _adj("C036", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 3, 3000, 9000)])
    g = checks.run_gate(adj, bundle, INDEX)
    assert _flags(g, AMT) == [] and len(_flags(g, "receipt_category_uncovered")) == 1


def test_u1_6_not_applied_to_deny_or_pol_a():
    bundle = _c036(C036_ITEMS)
    deny = _adj("C036", "POL-C", "第8條", decision="deny",
                calculation=[_capped("第10條", 6500, 60000).model_dump(mode="json")])
    assert _flags(checks.run_gate(deny, bundle, INDEX), AMT) == []
    bundle_a = ClaimBundle.model_validate(_items(_bundle_payload("C004"), [("藥費", "12200"), ("檢驗費", "6500")]))
    adj_a = _adj("C004", "POL-A", "第7條", calculation=[_capped("第10條", 6500, 60000).model_dump(mode="json")])
    assert _flags(checks.run_gate(adj_a, bundle_a, INDEX), AMT) == []


def test_u1_7_cap_scales_with_stay_groups_like_cap_sum():
    bundle = ClaimBundle.model_validate(_items(_bundle_payload("C059"), [("病房費", "22000"), ("藥費", "130000")]))
    assert len(checks.derive_facts(bundle).stay_groups) == 2  # 出院後逾十四日再住院:兩次住院
    # 兩次住院 cap 60,000 × 2 = 120,000;actual 120,000 已達 → 不旗標;actual 60,000 → 旗標
    bound = _adj("C059", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 5, 3000, 12000),
                                                        _capped_per_unit("第8條", 4, 3000, 10000),
                                                        _capped("第10條", 120000, 60000).model_dump(mode="json")])
    assert _flags(checks.run_gate(bound, bundle, INDEX), AMT) == []
    short = _adj("C059", "POL-C", "第8條", calculation=[_capped_per_unit("第8條", 5, 3000, 12000),
                                                        _capped_per_unit("第8條", 4, 3000, 10000),
                                                        _capped("第10條", 60000, 60000).model_dump(mode="json")])
    fl = _flags(checks.run_gate(short, bundle, INDEX), AMT)
    assert len(fl) == 1 and "120000" in fl[0].detail


# ---------- U2 B 既往症拒付源頭反查 ----------
def _deny(cid: str, policy: str, clauses: list[str], summary: str, diagnosis: str = "<診斷>"):
    payload = _bundle_payload(cid)
    payload["diagnosis_certificate"]["treatment_summary"]["value"] = summary
    payload["diagnosis_certificate"]["diagnosis"]["value"] = diagnosis
    bundle = ClaimBundle.model_validate(payload)
    d = _decision_payload(cid, policy, clauses[0])["adjudication"]
    cits = [_decision_payload(cid, policy, c)["policy"]["citations"][0] for c in clauses]
    d.update(decision="deny", calculation=[], citations=cits)
    adj = _adj(cid, policy, clauses[0], **{k: d[k] for k in ("decision", "calculation", "citations")})
    return adj, bundle


def test_u2_1_flags_c066_shape_recurrence_denied_by_clause_19_without_source_evidence():
    adj, bundle = _deny("C066", "POL-C", ["第19條", "第8條"],
                        "第1 次住院靜脈抗生素治療後出院;出院後同疾病復發 再入院續以抗生素治療", "急性膽管炎(復發)")
    g = checks.run_gate(adj, bundle, INDEX)
    fl = _flags(g, PRE)
    assert len(fl) == 1 and "第19條" in fl[0].detail
    assert g.final_decision is ClaimDecision.MANUAL_REVIEW and g.original_decision is ClaimDecision.DENY


def test_u2_2_c024_shape_diagnosed_years_ago_with_periodic_followup_is_evidence():
    adj, bundle = _deny("C024", "POL-A", ["第18條"], "病患肝硬化確診於四年前,定期本院肝膽腸胃科追蹤,本次因腹水住院")
    g = checks.run_gate(adj, bundle, INDEX)
    assert _flags(g, PRE) == [] and g.final_decision is ClaimDecision.DENY


def test_u2_3_history_keyword_is_evidence():
    adj, bundle = _deny("C005", "POL-A", ["第18條"], "病患過去五年有糖尿病病史,本次因血糖控制不良住院")
    assert _flags(checks.run_gate(adj, bundle, INDEX), PRE) == []


def test_u2_4_deny_without_pre_existing_clause_is_untouched_and_deny_flags_still_dominate():
    adj, bundle = _deny("C066", "POL-C", ["第23條"], "復發再入院")
    assert _flags(checks.run_gate(adj, bundle, INDEX), PRE) == []
    # 既往旗標與 DENY_FLAGS 並存:routes_to_deny 優先(現行語意不變)
    flags = [checks.GateFlag(check=PRE, detail="x"), checks.GateFlag(check="waiting_period_unmet", detail="y")]
    b4 = ClaimBundle.model_validate(_bundle_payload("C004"))
    assert checks.routes_to_deny(flags, b4) is True and checks.routes_to_manual(flags) is True


def test_u2_5_extended_regex_changes_no_approve_side_g5_hit_on_synthetic_corpus():
    old = re.compile(r"病史|長期.{0,6}追蹤|自\s?\d{4}[-/年].{0,4}起.{0,12}追蹤|投保前")
    import json
    diff: dict[str, bool] = {}
    for case in sorted((ROOT / "data" / "claims").iterdir()):
        claim = json.loads((case / "claim.json").read_text(encoding="utf-8"))
        dc = claim["documents"].get("diagnosis_certificate")
        if dc is None:
            continue
        s = dc["treatment_summary"]
        if bool(old.search(s)) != bool(checks._PRE_EXISTING.search(s)):
            diff[case.name] = json.loads((case / "truth.json").read_text(encoding="utf-8"))["decision"] == "approve"
    assert diff == {"C024": False}  # 只有 C024(真值 deny)新命中;approve 側 G5 零變動


def test_u2_6_pre_existing_clause_table_matches_policy_headings():
    assert set(checks.PRE_EXISTING_CLAUSE) == {"POL-A", "POL-B", "POL-C"}
    for code, clause in checks.PRE_EXISTING_CLAUSE.items():
        text = (ROOT / "data" / "policies" / f"{code}.md").read_text(encoding="utf-8")
        m = re.search(r"\*\*第([一二三四五六七八九十]+)條[(\uff08]既往症[)\uff09]\*\*", text)
        assert m is not None
        assert INDEX[code][clause].text and checks.normalize_clause_no(f"第{m.group(1)}條") == clause


# ---------- U3 run_tag ----------
def test_u3_review_run_tag_prefers_explicit_value_else_out_dir_name():
    from tools import run_pipeline as rp
    assert rp.review_run_tag("20261004T0647-abc1234", Path("/w/out")) == "20261004T0647-abc1234"
    assert rp.review_run_tag(None, Path("/w/out")) == "out"
    assert rp.review_run_tag("", Path("/w/out")) == "out"


def test_u3_cloud_run_passes_run_id_as_run_tag(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from tests.test_cloud_run import FakeStore, _fake_run
    from tools import cloud_run as cr
    holder: dict[str, Any] = {}
    monkeypatch.setenv("GIT_SHA", "deadbeefcafe")
    assert cr.main(["--review"], store=FakeStore(), run=_fake_run(holder),
                   now=datetime(2026, 10, 4, 6, 47, tzinfo=UTC), work_dir=tmp_path) == 0
    argv = holder["argv"]
    assert argv[argv.index("--run-tag") + 1] == "20261004T0647-deadbee"
