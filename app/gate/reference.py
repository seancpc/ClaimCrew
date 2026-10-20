"""條款金額參考表(SPEC 2026-10-01-c P2):Gate 逐行反查 LLM 填的單價 / 定額 / 限額 / 比例。

單一來源:數值自 app/datagen/variants.POLICY_PARAMS 導出(真值同源;tests/test_data_invariants 鎖住),
tests/test_reference.py 另驗每個金額出現在對應條款原文。v2 全量實證:模型把 cap 填成日額(C033)、
手術限額填 12,000(C055)、門診手術定額填成折半後金額(C028)——加總再正確也沒用,必須反查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.datagen.variants import POLICY_PARAMS
from app.gate.policy_index import normalize_clause_no
from app.schemas.claim import CalcKind, CalcLine

ONE = Decimal(1)
HALF = Decimal("0.5")


@dataclass(frozen=True)
class Rule:
    kinds: frozenset[CalcKind]
    unit: int | None = None  # per_unit / capped_per_unit 之單價
    lumps: frozenset[int] = field(default_factory=frozenset)  # lump_sum 允許定額(條款原值)
    caps: frozenset[int] = field(default_factory=frozenset)  # capped_actual 允許限額
    ratios: frozenset[Decimal] = frozenset({ONE})  # 搭配 lumps / caps / unit 的允許比例
    max_quantity: int | None = None
    # P4-4 雙表示:模型三次無視「填原值 + ratio 0.5」的 prompt,改接受「已折半定額 + ratio 1」(POL-B 門診/次高手術)
    half_lumps: frozenset[int] = field(default_factory=frozenset)


def _build() -> dict[str, dict[str, Rule]]:
    a, b, c = POLICY_PARAMS["POL-A"], POLICY_PARAMS["POL-B"], POLICY_PARAMS["POL-C"]
    pu, ls, ca, cpu = (frozenset({k}) for k in (CalcKind.PER_UNIT, CalcKind.LUMP_SUM,
                                                 CalcKind.CAPPED_ACTUAL, CalcKind.CAPPED_PER_UNIT))
    a_surg = frozenset(a["surgery"].values())
    b_surg = frozenset(b["surgery"].values())
    b_half = frozenset(int(Decimal(v) * HALF) for v in b_surg)
    c_ratios = frozenset({ONE, Decimal(str(c["non_nhi_factor"]))})
    c_surg_caps = frozenset(int(Decimal(c["surgery_base"]) * p) for p in c["surgery_pct"].values())
    return {
        "POL-A": {
            "第7條": Rule(pu, unit=a["daily"]),
            "第8條": Rule(pu, unit=a["icu_daily"]),
            "第9條": Rule(pu, unit=a["outpatient_visit"], max_quantity=a["outpatient_visits_cap"]),
            "第10條": Rule(ls, lumps=a_surg),
            "附錄一": Rule(ls, lumps=a_surg),
            "第11條": Rule(pu, unit=a["aux_daily"]),
        },
        "POL-B": {
            "第7條": Rule(ls, lumps=b_surg, ratios=frozenset({ONE, HALF}), half_lumps=b_half),  # 次高 50% 可掛第7條
            "附錄一": Rule(ls, lumps=b_surg, ratios=frozenset({ONE, HALF}), half_lumps=b_half),
            "第8條": Rule(ls, lumps=b_surg, ratios=frozenset({HALF}), half_lumps=b_half),  # 門診手術 = 同級 50%
            "第9條": Rule(ls, lumps=b_surg, ratios=frozenset({HALF}), half_lumps=b_half),  # 次高手術 50%
            "第12條": Rule(pu, unit=b["solace_daily"], max_quantity=b["solace_cap_days"]),
        },
        "POL-C": {
            "第8條": Rule(cpu, unit=c["room_daily_cap"], ratios=c_ratios),
            "第9條": Rule(ca, caps=c_surg_caps, ratios=c_ratios),
            "附錄一": Rule(ca, caps=c_surg_caps, ratios=c_ratios),
            "第10條": Rule(ca, caps=frozenset({c["misc_cap"]}), ratios=c_ratios),
            "第11條": Rule(ca, caps=frozenset({c["ambulance_cap"]}), ratios=c_ratios),
        },
    }


REFERENCE: dict[str, dict[str, Rule]] = _build()


def check_line(policy_code: str, line: CalcLine) -> list[tuple[str, str]]:
    """回傳 [(flag_code, detail)];空列表 = 通過。條號不在表內即短路為 calc_unknown_clause。"""
    rule = REFERENCE.get(policy_code, {}).get(normalize_clause_no(line.clause_no))
    tag = f"{policy_code} {line.clause_no}「{line.item}」"
    if rule is None:
        return [("calc_unknown_clause", f"{tag} 不在給付條款參考表內")]
    flags: list[tuple[str, str]] = []
    if line.kind not in rule.kinds:
        flags.append(("calc_kind_mismatch", f"{tag} kind={line.kind.value},允許 {sorted(k.value for k in rule.kinds)}"))
    if rule.unit is not None and line.kind in (CalcKind.PER_UNIT, CalcKind.CAPPED_PER_UNIT) \
            and line.unit_amount != rule.unit:
        flags.append(("calc_reference_mismatch", f"{tag} 單價 {line.unit_amount} ≠ 條款 {rule.unit}"))
    if rule.max_quantity is not None and line.quantity > rule.max_quantity:
        flags.append(("calc_reference_mismatch", f"{tag} 數量 {line.quantity} 超過條款上限 {rule.max_quantity}"))
    if line.kind is CalcKind.LUMP_SUM and rule.lumps:
        lump = int(line.lump_amount)
        if lump in rule.half_lumps and line.ratio == ONE:
            return flags  # 雙表示:已折半定額 × 1,等價於原值 × 0.5
        if lump not in rule.lumps:
            flags.append(("calc_reference_mismatch", f"{tag} 定額 {line.lump_amount} 不在條款金額 {sorted(rule.lumps)}"
                          + (f" 或折半金額 {sorted(rule.half_lumps)}(ratio 1)" if rule.half_lumps else "")))
    if line.kind is CalcKind.CAPPED_ACTUAL and rule.caps and int(line.cap) not in rule.caps:
        flags.append(("calc_reference_mismatch", f"{tag} 限額 {line.cap} 不在條款限額 {sorted(rule.caps)}"))
    if line.ratio not in rule.ratios:
        flags.append(("calc_ratio_invalid", f"{tag} 比例 {line.ratio} 不在允許值 {sorted(rule.ratios)}"))
    return flags


def is_full_surgery_line(policy_code: str, line: CalcLine) -> bool:
    """S3 配套:ratio 1 且定額屬條款全額集才算「全額手術行」;折半表示(7500 × 1)與 0.5 行皆不計入每段上限。"""
    rule = REFERENCE.get(policy_code, {}).get(normalize_clause_no(line.clause_no))
    if rule is None or line.kind is not CalcKind.LUMP_SUM or line.ratio != ONE:
        return False
    return int(line.lump_amount) in rule.lumps
# SPEC-w W2:POL-C 第9條手術限額 = 捌萬元 × 等級百分比(第一級 25%、第二級 50%、第三級 100%;測試自條文解析比對)
POLC_SURGERY_CAP: dict[int, int] = {1: 20000, 2: 40000, 3: 80000}
