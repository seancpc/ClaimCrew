"""SPEC-ag:POL-A 第23條 90 日延長等待期提醒只在附錄二特定疾病 ICD 命中時觸發(使用者確認對照表 2026-10-07)。
ICD 缺漏或無法解析 → 保守仍提醒。C061(J96.00;S72.001;S32.000)不再每輪誤攔。"""
from __future__ import annotations

import pytest

from app.gate import checks


@pytest.mark.parametrize("icd, hit", [
    ("C34.90", True), ("C00", True), ("C97", True), ("D05.1", False),   # 1 惡性腫瘤 C00–C97
    ("I21.9", True), ("I20.0", True), ("I25.10", True), ("I26.9", False),  # 2 缺血性心臟病 I20–I25
    ("I63.9", True), ("I60", True), ("I69.3", True), ("I70.0", False),  # 3 腦血管 I60–I69
    ("K70.30", True), ("K70.4", False), ("K74.60", True), ("K74.3", True), ("K74.2", False),  # 4 肝硬化
    ("N18.3", True), ("N18.6", True), ("N18.2", False),                # 5 CKD 第三期以上
    ("E11.621", True), ("E08.9", True), ("E13", True), ("E14", False),  # 6 糖尿病
    ("J96.00;S72.001;S32.000", False), ("K80.00;E11.9", True),         # 多碼:任一命中
])
def test_ag1_appendix2_icd_mapping(icd, hit):
    assert checks.appendix2_match(icd) is hit


@pytest.mark.parametrize("icd", ["", "   ", "未填", "X"])
def test_ag1_unparsable_icd_is_conservative(icd):
    assert checks.appendix2_match(icd) is None


def _flags(cid: str) -> list[str]:
    from app.gate.policy_index import load_policy_index
    from tests.test_spec_p import _adj
    from tests.test_spec_w import _bundle
    return [f.check for f in checks.run_gate(_adj(cid, "POL-A", "第7條"), _bundle(cid), load_policy_index()).flags]


def test_ag2_c061_not_in_appendix2_not_flagged():
    assert "special_waiting_period_review" not in _flags("C061")


def test_ag2_c022_myocardial_infarction_still_flagged():
    assert "special_waiting_period_review" in _flags("C022")


def test_ag2_missing_icd_still_flagged_conservatively():
    from app.gate.policy_index import load_policy_index
    from app.schemas.claim import ClaimBundle
    from tests.test_pipeline import _bundle_payload
    from tests.test_spec_p import _adj
    p = _bundle_payload("C022")
    p["diagnosis_certificate"]["icd_code"]["value"] = ""
    flags = [f.check for f in checks.run_gate(_adj("C022", "POL-A", "第7條"), ClaimBundle.model_validate(p),
                                              load_policy_index()).flags]
    assert "special_waiting_period_review" in flags
