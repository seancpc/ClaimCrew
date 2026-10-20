"""結構化計算明細 → 金額(SPEC 2026-10-01-b B1):LLM 填算式材料,程式算子項與總額。

子項公式(依 CalcKind):
- per_unit       = quantity × unit_amount × ratio
- capped_actual  = min(max(actual_amount − deduction, 0), cap) × ratio(先扣他保再套限額,順序不得顛倒)
- capped_per_unit = min(max(actual_amount − deduction, 0), quantity × unit_amount) × ratio(逐日限額,POL-C 第8條)
- lump_sum       = lump_amount × ratio
總額 = Σ 子項,向下取整至元。
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from app.gate.policy_index import normalize_clause_no
from app.schemas.claim import CalcKind, CalcLine


def cap_group_clause(line: CalcLine) -> str:
    """限額歸組條號:POL-C 手術費可掛第9條或附錄一(手術等級表),兩者同一限額(第9條「於下列限額內」)。

    附錄一只有 POL-C 為限額型(POL-A/B 為 lump_sum 定額)→ capped_actual + 附錄一 一律歸第9條。
    2026-10-10 線上 C069:手術費掛附錄一、麻醉費掛第9條,各套 20,000 → 合併與上限加總皆漏接、放行 +1,300。
    """
    clause = normalize_clause_no(line.clause_no)
    return "第9條" if clause == "附錄一" and line.kind is CalcKind.CAPPED_ACTUAL else clause


def compute_line(line: CalcLine) -> Decimal:
    if line.kind is CalcKind.PER_UNIT:
        base = Decimal(line.quantity) * line.unit_amount
    elif line.kind is CalcKind.CAPPED_ACTUAL:
        base = min(max(line.actual_amount - line.deduction, Decimal(0)), line.cap)
    elif line.kind is CalcKind.CAPPED_PER_UNIT:
        base = min(max(line.actual_amount - line.deduction, Decimal(0)), Decimal(line.quantity) * line.unit_amount)
    else:
        base = line.lump_amount
    return base * line.ratio


def total_amount(lines: list[CalcLine]) -> Decimal:
    subtotal = sum((compute_line(ln) for ln in lines), Decimal(0))
    return subtotal.to_integral_value(rounding=ROUND_DOWN)


def normalize_calculation(lines: list[CalcLine], groups: int = 1) -> list[CalcLine]:
    """SPEC-p P1:同 (條號, capped_actual, cap, ratio) 的多行合併為一行(actual / deduction 加總、item 以「+」串接)。

    groups = 同一次住院分組數(derive_facts.stay_groups);≥2 時各組各有限額、LLM 每組一行是合法拆行(C059),不合併。

    限額型條款為「合計受限」,LLM 拆行各自套限額會多付(v4/v7 C003 手術費 + 麻醉費);合併只動材料的擺法、
    不改收據事實(Σactual 不變),冪等。其他 kind 原樣保留,順序以首次出現為準。
    """
    if groups != 1:
        return list(lines)
    merged: dict[tuple[str, Decimal, Decimal], int] = {}
    out: list[CalcLine] = []
    for ln in lines:
        if ln.kind is not CalcKind.CAPPED_ACTUAL:
            out.append(ln)
            continue
        key = (cap_group_clause(ln), ln.cap, ln.ratio)
        if key in merged:
            i = merged[key]
            prev = out[i]
            out[i] = prev.model_copy(update={
                "item": f"{prev.item}+{ln.item}",
                "actual_amount": prev.actual_amount + ln.actual_amount,
                "deduction": prev.deduction + ln.deduction,
            })
        else:
            merged[key] = len(out)
            out.append(ln)
    return out
