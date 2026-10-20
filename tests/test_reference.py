"""條款金額參考表與逐行反查(SPEC 2026-10-01-c P2)。"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.datagen.variants import POLICY_PARAMS
from app.gate.calc import compute_line
from app.gate.reference import REFERENCE, check_line
from app.schemas.claim import CalcKind
from tests.test_calc import _line

POLICIES = Path(__file__).resolve().parents[1] / "data" / "policies"
CN = {"0": "零", "1": "壹", "2": "貳", "3": "參", "4": "肆", "5": "伍", "6": "陸", "7": "柒", "8": "捌", "9": "玖"}


def _codes(flags):
    return [c for c, _ in flags]


# --- 新 kind:逐日限額 ---
def test_capped_per_unit_is_min_of_actual_and_quantity_times_unit():
    # C033 病房費:min(35000, 10 × 3000) = 30000
    assert compute_line(_line("capped_per_unit", quantity=10, unit_amount="3000", actual_amount="35000")) == 30000
    assert compute_line(_line("capped_per_unit", quantity=10, unit_amount="3000", actual_amount="25000")) == 25000
    assert compute_line(_line("capped_per_unit", quantity=3, unit_amount="3000", actual_amount="9000",
                              deduction="1000", ratio="0.65")) == Decimal("5200")  # (9000−1000)=8000 ≤ 9000 → ×0.65
    assert CalcKind.CAPPED_PER_UNIT.value == "capped_per_unit"


# --- 參考表 ↔ 參數單一來源 / 條款原文 ---
def test_reference_values_match_policy_params():
    a, b, c = POLICY_PARAMS["POL-A"], POLICY_PARAMS["POL-B"], POLICY_PARAMS["POL-C"]
    assert REFERENCE["POL-A"]["第7條"].unit == a["daily"] and REFERENCE["POL-A"]["第8條"].unit == a["icu_daily"]
    assert REFERENCE["POL-A"]["第11條"].unit == a["aux_daily"]
    assert REFERENCE["POL-A"]["第9條"].unit == a["outpatient_visit"]
    assert REFERENCE["POL-A"]["第10條"].lumps == set(a["surgery"].values())
    assert REFERENCE["POL-B"]["第7條"].lumps == set(b["surgery"].values()) == REFERENCE["POL-B"]["第8條"].lumps
    assert REFERENCE["POL-B"]["第12條"].unit == b["solace_daily"] and REFERENCE["POL-B"]["第12條"].max_quantity == 30
    assert REFERENCE["POL-C"]["第8條"].unit == c["room_daily_cap"]
    assert REFERENCE["POL-C"]["第9條"].caps == {int(Decimal(c["surgery_base"]) * p) for p in c["surgery_pct"].values()}
    assert REFERENCE["POL-C"]["第10條"].caps == {c["misc_cap"]}
    assert REFERENCE["POL-C"]["第11條"].caps == {c["ambulance_cap"]}
    assert Decimal("0.65") in REFERENCE["POL-C"]["第10條"].ratios


@pytest.mark.parametrize("policy", ["POL-A", "POL-B", "POL-C"])
def test_reference_amounts_appear_in_clause_text(policy):
    """表內每個金額(國字大寫)必須能在對應條款原文找到,防表與條文漂移。"""
    text = (POLICIES / f"{policy}.md").read_text(encoding="utf-8")
    for clause_no, rule in REFERENCE[policy].items():
        if clause_no == "附錄一":
            continue  # 附錄一為手術等級表(名稱),金額在第10/7/9條
        amounts = ({rule.unit} if rule.unit else set()) | rule.lumps | rule.caps
        if policy == "POL-C" and clause_no == "第9條":
            amounts = {80000}  # 條文寫「捌萬元之百分之 25/50/100」,限額為導出值
            assert "百分之二十五" in text and "百分之五十" in text and "百分之一百" in text
        for amount in amounts:
            assert _to_chinese(amount) in text, (policy, clause_no, amount)


def _to_chinese(n: int) -> str:
    # 條款金額皆為「貳仟 / 貳萬伍仟 / 壹拾萬 / 捌萬 / 陸萬 / 伍佰」形式
    units = [(100000, "拾萬"), (10000, "萬"), (1000, "仟"), (100, "佰")]
    out = ""
    for base, name in units:
        if n >= base:
            d, n = divmod(n, base)
            out += (CN[str(d)] if name != "拾萬" else CN[str(d)]) + name
    return out


# --- 逐行反查 ---
def test_matching_lines_pass():
    ok = [
        ("POL-A", _line("per_unit", clause_no="第7條", quantity=6, unit_amount="2000")),
        ("POL-A", _line("per_unit", clause_no="第8條", quantity=7, unit_amount="4000")),
        ("POL-A", _line("lump_sum", clause_no="第10條", lump_amount="25000")),
        ("POL-A", _line("lump_sum", clause_no="附錄一", lump_amount="50000")),
        ("POL-B", _line("lump_sum", clause_no="第7條", lump_amount="100000")),
        ("POL-B", _line("lump_sum", clause_no="第8條", lump_amount="5000", ratio="0.5")),
        ("POL-B", _line("lump_sum", clause_no="第9條", lump_amount="30000", ratio="0.5")),
        ("POL-B", _line("per_unit", clause_no="第12條", quantity=30, unit_amount="1000")),
        ("POL-C", _line("capped_per_unit", clause_no="第8條", quantity=10, unit_amount="3000", actual_amount="35000")),
        ("POL-C", _line("capped_actual", clause_no="第9條", actual_amount="62000", cap="40000")),
        ("POL-C", _line("capped_actual", clause_no="第10條", actual_amount="41500", cap="60000", ratio="0.65")),
        ("POL-C", _line("capped_actual", clause_no="第11條", actual_amount="1800", cap="2000")),
        ("POL-C", _line("capped_per_unit", clause_no="8", actual_amount="1", quantity=1, unit_amount="3000")),
    ]
    for pol, ln in ok:
        assert check_line(pol, ln) == [], (pol, ln.clause_no)


@pytest.mark.parametrize("policy, line, expected", [
    ("POL-A", _line("per_unit", clause_no="第7條", quantity=6, unit_amount="2500"), "calc_reference_mismatch"),
    ("POL-A", _line("lump_sum", clause_no="第7條", lump_amount="2000"), "calc_kind_mismatch"),
    ("POL-A", _line("per_unit", clause_no="第99條", quantity=1, unit_amount="1"), "calc_unknown_clause"),
    ("POL-A", _line("per_unit", clause_no="第7條", quantity=6, unit_amount="2000", ratio="0.5"), "calc_ratio_invalid"),
    ("POL-A", _line("per_unit", clause_no="第9條", quantity=7, unit_amount="500"), "calc_reference_mismatch"),  # >6 次
    ("POL-B", _line("lump_sum", clause_no="第8條", lump_amount="2500", ratio="0.5"),
     "calc_reference_mismatch"),  # C028 形
    ("POL-B", _line("lump_sum", clause_no="第8條", lump_amount="5000", ratio="1"), "calc_ratio_invalid"),
    ("POL-C", _line("per_unit", clause_no="第8條", quantity=10, unit_amount="3000"), "calc_kind_mismatch"),  # v2 13 行
    ("POL-C", _line("capped_actual", clause_no="第9條", actual_amount="70000", cap="12000"),
     "calc_reference_mismatch"),  # C055
    ("POL-C", _line("capped_actual", clause_no="第8條", actual_amount="35000", cap="3000"),
     "calc_kind_mismatch"),  # C033
    ("POL-C", _line("capped_actual", clause_no="第10條", actual_amount="1", cap="60000", ratio="0.5"),
     "calc_ratio_invalid"),
    ("POL-X", _line("per_unit", clause_no="第7條", quantity=1, unit_amount="1"), "calc_unknown_clause"),
])
def test_mismatching_lines_flagged(policy, line, expected):
    assert expected in _codes(check_line(policy, line))


def test_unknown_clause_short_circuits_other_checks():
    flags = check_line("POL-A", _line("lump_sum", clause_no="第99條", lump_amount="1", ratio="0.5"))
    assert _codes(flags) == ["calc_unknown_clause"]


# --- P4-4 POL-B 門診/次高手術雙表示(SPEC 2026-10-01-d;prompt 三次無效,改程式接受)---
@pytest.mark.parametrize("clause", ["第8條", "第9條", "第7條", "附錄一"])
@pytest.mark.parametrize("lump, ratio",
                         [("15000", "0.5"), ("7500", "1"), ("2500", "1"), ("50000", "1"), ("100000", "0.5")])
def test_pol_b_accepts_full_amount_half_ratio_or_halved_amount_full_ratio(clause, lump, ratio):
    assert check_line("POL-B", _line("lump_sum", clause_no=clause, lump_amount=lump, ratio=ratio)) == []


@pytest.mark.parametrize("clause, lump, ratio", [
    ("第8條", "2500", "0.5"),   # 折半再折半(C028 v2 形)
    ("第8條", "7500", "0.5"),
    ("第8條", "5000", "1"),     # 門診手術不得全額(全額集 × ratio 1 只在第7條/附錄一合法)
    ("第9條", "5000", "1"),     # 次高手術不得全額(30000×1 因與第四級折半同值而無法分辨,屬 SPEC-d 假設 3 接受之模糊)
    ("第7條", "9999", "1"),
])
def test_pol_b_still_rejects_double_halving_and_full_outpatient(clause, lump, ratio):
    assert check_line("POL-B", _line("lump_sum", clause_no=clause, lump_amount=lump, ratio=ratio)) != []


def test_pol_b_full_amount_lines_count_only_full_set_with_ratio_one():
    # S3 配套:7500×1(折半表示)不算「全額手術行」,C051 單段兩行(30000×1 + 7500×1)不得旗標
    from app.gate.reference import is_full_surgery_line
    assert is_full_surgery_line("POL-B", _line("lump_sum", clause_no="第7條", lump_amount="30000")) is True
    assert is_full_surgery_line("POL-B", _line("lump_sum", clause_no="附錄一", lump_amount="7500")) is False
    half = _line("lump_sum", clause_no="第7條", lump_amount="30000", ratio="0.5")
    assert is_full_surgery_line("POL-B", half) is False
    assert is_full_surgery_line("POL-A", _line("lump_sum", clause_no="第10條", lump_amount="25000")) is True
