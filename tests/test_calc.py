"""結構化計算明細與程式加總(SPEC 2026-10-01-b B1)。"""

from decimal import Decimal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.gate.calc import compute_line, total_amount
from app.schemas.claim import AdjudicationDraft, AdjudicationResult, CalcKind, CalcLine, DecisionResult


def _line(kind: str, **kw) -> CalcLine:
    base = {"item": "<項目>", "clause_no": "第7條", "kind": kind, "quantity": 0, "unit_amount": "0",
            "actual_amount": "0", "deduction": "0", "cap": "0", "lump_amount": "0", "ratio": "1"}
    base.update(kw)  # kw 可覆寫 kind(非法 kind 測試)
    return CalcLine.model_validate(base)


# --- B1-3 手算 ---
def test_per_unit_lines_sum_to_c001_truth():
    lines = [_line("per_unit", quantity=6, unit_amount="2000"), _line("per_unit", quantity=6, unit_amount="1000")]
    assert [compute_line(ln) for ln in lines] == [Decimal(12000), Decimal(6000)]
    assert total_amount(lines) == Decimal(18000)


def test_lump_plus_per_unit_c030_truth():
    assert total_amount([_line("lump_sum", lump_amount="100000"),
                         _line("per_unit", quantity=30, unit_amount="1000")]) == Decimal(130000)


def test_capped_actual_with_deduction_and_ratio():
    # min(max(15000 − 5000, 0), 3000) × 0.65 = 1950
    assert compute_line(_line("capped_actual", actual_amount="15000", deduction="5000", cap="3000",
                              ratio="0.65")) == Decimal(1950)


def test_capped_actual_deduction_exceeding_actual_floors_at_zero():
    assert compute_line(_line("capped_actual", actual_amount="1000", deduction="5000", cap="3000")) == 0


def test_total_floors_to_whole_dollars_and_ignores_unused_fields():
    # 50% 的 25 元 = 12.5 → 12;未用欄位(lump 行帶 quantity)不得影響
    assert total_amount([_line("per_unit", quantity=1, unit_amount="25", ratio="0.5")]) == Decimal(12)
    assert compute_line(_line("lump_sum", lump_amount="100", quantity=9, unit_amount="9", cap="1")) == Decimal(100)


def test_empty_calculation_totals_zero():
    assert total_amount([]) == 0


# --- 型別封閉 ---
@pytest.mark.parametrize("bad", [{"ratio": "abc"}, {"ratio": "-0.5"}, {"ratio": "1.5"}, {"kind": "guess"},
                                 {"quantity": -1}, {"quantity": 1.5}, {"unit_amount": "-1"}])
def test_invalid_lines_rejected(bad):
    with pytest.raises(ValidationError):
        _line(**{"kind": "per_unit", **bad})


def test_money_normalization_applies_to_calc_fields():
    assert compute_line(_line("per_unit", quantity=2, unit_amount="NT$2,000元")) == Decimal(4000)


def test_calc_kind_values_are_closed():
    # SPEC 2026-10-01-c P2 新增 capped_per_unit(逐日限額)
    assert {k.value for k in CalcKind} == {"per_unit", "capped_actual", "lump_sum", "capped_per_unit"}


# --- B1-1 Draft / Result 分工 ---
def test_draft_has_no_approved_amount_and_result_adds_it():
    assert "approved_amount" not in AdjudicationDraft.model_fields
    assert "approved_amount" in AdjudicationResult.model_fields
    assert "calculation" in AdjudicationDraft.model_fields
    assert DecisionResult.model_fields["adjudication"].annotation is AdjudicationDraft
    assert issubclass(AdjudicationResult, AdjudicationDraft)


# --- B1-6 property ---
money = st.decimals(min_value=0, max_value=10**7, places=0)
# 單價偏向條款量級(500~4000),讓「日數 × 單價 < 實際金額」的逐日限額生效分支穩定被生成(否則 M18 型變異只偶爾被殺)
unit_st = st.one_of(st.sampled_from([Decimal(x) for x in (0, 500, 1000, 2000, 3000, 4000)]), money)
ratio = st.decimals(min_value=0, max_value=1, places=2)
line_st = st.builds(
    lambda kind, q, u, a, d, c, lp, r: _line(kind, quantity=q, unit_amount=str(u), actual_amount=str(a),
                                            deduction=str(d), cap=str(c), lump_amount=str(lp), ratio=str(r)),
    st.sampled_from(["per_unit", "capped_actual", "lump_sum", "capped_per_unit"]), st.integers(0, 365),
    unit_st, money, money, money, money, ratio,
)


@given(st.lists(line_st, max_size=6))
@settings(max_examples=100, deadline=None)
def test_property_total_is_sum_of_lines_nonnegative_and_integer(lines):
    subtotal = sum((compute_line(ln) for ln in lines), Decimal(0))
    t = total_amount(lines)
    assert t == subtotal.to_integral_value(rounding="ROUND_DOWN")
    assert t >= 0 and t == int(t)
    assert all(compute_line(ln) >= 0 for ln in lines)


@given(line_st)
@example(_line("capped_per_unit", quantity=10, unit_amount="3000", actual_amount="35000"))  # 逐日限額生效分支必測(M18)
@example(_line("capped_actual", actual_amount="62000", deduction="0", cap="40000"))  # 限額生效分支必測(M3)
@settings(max_examples=150, deadline=None)
def test_property_line_bounds(line):
    v = compute_line(line)
    if line.kind is CalcKind.CAPPED_ACTUAL:
        assert v <= line.cap * line.ratio and v <= max(line.actual_amount - line.deduction, 0) * line.ratio
    elif line.kind is CalcKind.CAPPED_PER_UNIT:  # 逐日限額:等於兩個上界(實際金額、日數 × 每日限額)之較小者 × 比例
        assert v == min(max(line.actual_amount - line.deduction, 0), line.quantity * line.unit_amount) * line.ratio
    elif line.kind is CalcKind.PER_UNIT:
        assert v == line.quantity * line.unit_amount * line.ratio
    else:
        assert v == line.lump_amount * line.ratio
