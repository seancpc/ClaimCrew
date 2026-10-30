"""SPEC-ae 階段 1:①第24條(POL-B 第21條)等待期內已診斷 ②放行抽樣稽核 ③issue_date 不參與信心分流 ④引文仟/千同義。"""
from __future__ import annotations

from app.gate import checks
from app.gate.policy_index import load_policy_index
from app.review import card as rc
from app.schemas.claim import ClaimBundle, ClaimDecision
from tests.test_pipeline import _bundle_payload
from tests.test_review import _output
from tests.test_spec_p import _adj
from tests.test_spec_w import _bundle

INDEX = load_policy_index()
FLAG = "waiting_period_prior_diagnosis"


def _hits(cid: str, policy: str, b: ClaimBundle, decision: str = "approve") -> list:
    adj = _adj(cid, policy, "第7條", decision=decision)
    return [f for f in checks.run_gate(adj, b, INDEX).flags if f.check == FLAG]


# ---------- ① 等待期內已診斷 ----------
def test_ae1_pol_c_c058_shape_flagged_manual():
    b = _bundle("C058")
    adj = _adj("C058", "POL-C", "第8條")
    g = checks.run_gate(adj, b, INDEX)
    assert [f for f in g.flags if f.check == FLAG] and g.final_decision is not ClaimDecision.APPROVE


def test_ae1_pol_a_c049_shape_flagged():
    assert _hits("C049", "POL-A", _bundle("C049"))


def test_ae1_pol_b_flagged():
    b = _bundle("C029", "病患於 2024-11-20 門診診斷膽結石,今因膽囊炎入院行膽囊切除術,住院5日")
    assert _hits("C029", "POL-B", b)


def test_ae1_injury_not_flagged():
    p = _bundle_payload("C058")
    p["diagnosis_certificate"]["icd_code"]["value"] = "S72.001A"
    assert _hits("C058", "POL-C", ClaimBundle.model_validate(p)) == []


def test_ae1_date_outside_window_not_flagged():
    b = _bundle("C058", "病患於 2026-05-30 本院門診首次診斷第2型糖尿病,今因左足感染入院")  # 生效後第 35 日
    assert _hits("C058", "POL-C", b) == []


def test_ae1_date_without_diagnosis_word_not_flagged():
    b = _bundle("C058", "2026-05-10 騎乘自行車跌倒,今因左足感染入院")
    assert _hits("C058", "POL-C", b) == []


def test_ae1_event_still_in_waiting_period_not_flagged():
    b = _bundle("C021", "病患於 2026-06-25 門診診斷肺炎,今入院治療")  # 事件日距生效 15 日,由 waiting_period_unmet 處理
    assert _hits("C021", "POL-A", b) == []


def test_ae1_non_approve_not_checked():
    assert _hits("C058", "POL-C", _bundle("C058"), decision="manual_review") == []


# ---------- ② 放行抽樣稽核 ----------
def _approved(cid: str):
    return _output(cid, "POL-A", "第7條")


def test_ae2_audit_selection_reproducible_and_rate_bounded():
    ids = [f"C{i:03d}" for i in range(1, 201)]
    a = [c for c in ids if rc.audit_selected(c, "run-x", 0.10)]
    b = [c for c in ids if rc.audit_selected(c, "run-x", 0.10)]
    assert a == b and 8 <= len(a) <= 35
    assert a != [c for c in ids if rc.audit_selected(c, "run-y", 0.10)]


def test_ae2_rate_zero_selects_nothing_and_default_off():
    out = _approved("C001")
    assert not rc.audit_selected("C001", "r", 0.0)
    assert rc.should_review(out) is False


def test_ae2_only_auto_approved_are_audited():
    out = _approved("C001")
    assert rc.should_review(out, audit_rate=1.0, run_tag="r") is True
    manual = _output("C001", "POL-A", "第7條", decision="deny", final=ClaimDecision.DENY)
    assert rc.should_review(manual, audit_rate=1.0, run_tag="r") is False


def test_ae2_audit_card_subject_marked():
    p = _bundle_payload("C001")
    card = rc.build_review_card(_approved("C001"), ClaimBundle.model_validate(p), audit=True)
    assert card.subject.startswith("[ClaimCrew 抽樣稽核]")
    plain = rc.build_review_card(_approved("C001"), ClaimBundle.model_validate(p))
    assert plain.subject.startswith("[ClaimCrew 人工覆核]")


# ---------- ③ issue_date 不參與信心分流 ----------
def _low(field_path: tuple[str, str]) -> list:
    p = _bundle_payload("C001")
    p[field_path[0]][field_path[1]]["confidence"] = 0.3
    adj = _adj("C001", "POL-A", "第7條")
    return [f for f in checks.run_gate(adj, ClaimBundle.model_validate(p), INDEX).flags
            if f.check == "low_confidence_fields"]


def test_ae3_low_issue_date_not_flagged():
    """SPEC-ag 重試 ③:SPEC-af 補上合併限額少付後,issue_date 已非任何模型 approve 錯誤之唯一防線(固定解法 31 檢查)。"""
    assert _low(("diagnosis_certificate", "issue_date")) == []


def test_ae3_other_low_field_still_flagged():
    assert _low(("diagnosis_certificate", "diagnosis"))


# ---------- ④ 仟/千 ----------
def test_ae4_qian_variant_matches():
    q = ("被保險人依第七條約定住院診療者,本公司按其實際住院日數,"
         "每日給付「住院醫療輔助保險金」新臺幣壹千元,作為出院療養之用。")
    assert checks.quote_matches(q, INDEX["POL-A"]["第11條"].text)


def test_ae4_rewrite_still_fails():
    q = "本公司每日給付住院醫療輔助保險金新臺幣壹千元。"
    assert not checks.quote_matches(q, INDEX["POL-A"]["第11條"].text)
