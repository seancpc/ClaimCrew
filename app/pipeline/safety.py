"""SPEC-v(方案 A 影子模式):Content Safety groundedness + 四類掃描,結果記 PipelineOutput.safety,不碰判定。

被檢文字 = reasoning + citations[].relevance(皆先以 bundle 已知姓名遮罩——送外部服務的文字也是「渲染產物」,固定解法 21);
grounding sources = [診斷書診斷+治療摘要, 收據項目, *引用條款原文]
(條款原文不入 sources 會把所有「依第 N 條…」判成無據)。
任何例外只記 SafetyRecord.error(遮 sig=),不得中斷管線。
"""
from __future__ import annotations

import re
import time
from typing import Protocol

from app.gate.policy_index import Clause, normalize_clause_no, normalize_policy_id
from app.pipeline.derived import derive_facts
from app.schemas.claim import ClaimBundle, GroundednessResult, PipelineOutput, SafetyRecord

# 桌機 2026-10-05 實測(cs_smoke --sep-test):detectGroundedness 只認 ASCII '.' 為句界;中文「。」與換行會把整段當一塊,
# 混合塊的判定不穩定(同段 \n 版判 1、「。」版判 0)。故送出前把中文句界正規化為 ".\n",比例按字元加權逐句計。
_SENTENCE_END = re.compile(r"[。!！?？;；\n.]+")


def segment_sentences(text: str) -> str:
    parts = [p.strip() for p in _SENTENCE_END.split(text)]
    return "\n".join(f"{p}." for p in parts if p)


# 2026-10-05 盤點(v9 A 269 句無據):98 句含條號、45 句阿拉伯數字 vs 條款大寫金額/中文日數——API 字面比對不會把
# 「2000元」對上「貳仟元」、「依第13條」對上無標籤的條文。故 sources 做確定性正規化:條款加標籤、數字旁註兩種寫法。
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "貳": 2, "兩": 2, "三": 3, "參": 3, "叁": 3, "四": 4, "肆": 4,
             "五": 5, "伍": 5, "六": 6, "陸": 6, "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9}
_CN_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_NUM_CHARS = "".join(_CN_DIGIT) + "".join(_CN_UNIT) + "萬"
_CN_AMOUNT = re.compile(rf"([{_CN_NUM_CHARS}]+)(元|日)(?!\()")


def chinese_to_int(cn: str) -> int:
    """中文/大寫數字 → 整數(支援到萬:貳萬伍仟、壹拾萬、九十、十四、三十)。"""
    total = section = number = 0
    for ch in cn:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (number or 1) * _CN_UNIT[ch]
            number = 0
        elif ch == "萬":
            total += (section + number or 1) * 10_000
            section = number = 0
    return total + section + number


def int_to_chinese(n: int) -> str:
    """1~99 → 中文數字(六、十、十四、二十、九十七);其他回阿拉伯字串。"""
    digits = "零一二三四五六七八九"
    if not 1 <= n <= 99:
        return str(n)
    if n < 10:
        return digits[n]
    tens, units = divmod(n, 10)
    return ("" if tens == 1 else digits[tens]) + "十" + (digits[units] if units else "")


def annotate_numerals(text: str) -> str:
    """「貳仟元」→「貳仟元(2000元)」、「九十日」→「九十日(90日)」;已有括號旁註者不重複;「次」不旁註。"""
    return _CN_AMOUNT.sub(lambda m: f"{m.group(1)}{m.group(2)}({chinese_to_int(m.group(1))}{m.group(2)})", text)


def _days(n: int | None) -> str:
    return f"{n} 日" if n is None or not 1 <= n <= 99 else f"{n} 日({int_to_chinese(n)}日)"


class SafetyClient(Protocol):
    def detect_groundedness(self, text: str, sources: list[str], domain: str = "Medical") -> GroundednessResult: ...

    def analyze_text(self, text: str) -> dict[str, int]: ...


def _derived_source(bundle: ClaimBundle) -> str:
    """v9 量測(2026-10-05):被判無據的句子多為「住院 N 日」「距生效 N 日」「合併 N 日」等程式推導事實——
    它們是 prompt 注入的材料,理當算有據;calculation 行刻意不入(避免模型自己填的數字自我背書)。"""
    f = derive_facts(bundle)
    app_ = bundle.claim_application
    lines = [f"保單號碼:{app_.policy_number.value}", f"保單生效日:{app_.policy_effective_date.value}",
             f"申請日:{app_.application_date.value}", f"得為申請之日:{f.claim_event_date}",
             f"事件距生效日:{_days(f.days_since_effective)}",
             f"申請距得為申請之日:{_days(f.days_event_to_application)}",
             f"住院總日數(含入出院日):{_days(f.total_days)}", f"加護病房日數:{_days(f.total_icu_days)}",
             f"收據合計:{f.receipt_total}", f"他保已給付:{'有' if f.other_insurer_paid_any else '無'}"]
    for i, a in enumerate(f.admissions, 1):
        gap = f";與前段相隔 {_days(a.gap_days_after_previous)}" if a.gap_days_after_previous is not None else ""
        lines.append(f"住院第{i}段:{a.admission_date} 至 {a.discharge_date},{_days(a.days_inclusive)}{gap}")
    for i, days in enumerate(f.stay_group_days, 1):
        lines.append(f"同一次住院第{i}組合計:{_days(days)}")
    return "\n".join(lines)


def build_inputs(output: PipelineOutput, bundle: ClaimBundle, index: dict[str, dict[str, Clause]],
                 *, reasoning_only: bool = False) -> tuple[str, list[str]]:
    from app.review.card import _names, _redact  # 遮罩規則單一來源(card.py);延後 import 避免循環

    names = _names(bundle)
    adj = output.adjudication
    parts = [_redact(adj.reasoning, names)]
    if not reasoning_only:
        parts += [_redact(c.relevance, names) for c in adj.citations]
    text = segment_sentences("\n".join(parts))
    dc, rc = bundle.diagnosis_certificate, bundle.medical_receipt
    doc = _redact(f"診斷:{dc.diagnosis.value}\n治療摘要:{dc.treatment_summary.value}", names)
    receipt = "收據項目:" + "、".join(f"{it.item_name} {it.amount}" for it in rc.items)
    clauses: list[str] = []
    for c in adj.citations:
        clause = index.get(normalize_policy_id(c.policy_id), {}).get(normalize_clause_no(c.clause_no))
        if clause is None:
            continue
        labelled = f"{clause.policy_code} {clause.clause_no}({clause.title}):{annotate_numerals(clause.text)}"
        if labelled not in clauses:
            clauses.append(labelled)
    return text, [doc, receipt, _derived_source(bundle), *clauses]


def safety_check(output: PipelineOutput, bundle: ClaimBundle, client: SafetyClient,
                 index: dict[str, dict[str, Clause]], *, reasoning_only: bool = False) -> SafetyRecord:
    from app.review.card import mask_url

    t0 = time.time()
    errors: list[str] = []
    text, sources = build_inputs(output, bundle, index, reasoning_only=reasoning_only)
    groundedness: GroundednessResult | None = None
    harm: dict[str, int] | None = None
    try:
        groundedness = client.detect_groundedness(text, sources)
    except Exception as e:  # noqa: BLE001 — 影子欄位不得中斷管線
        errors.append(f"groundedness {type(e).__name__}: {e}")
    try:
        harm = client.analyze_text(text)
    except Exception as e:  # noqa: BLE001
        errors.append(f"analyze {type(e).__name__}: {e}")
    return SafetyRecord(invoked=True, groundedness=groundedness, harm=harm,
                        error=mask_url("; ".join(errors))[:300], elapsed_ms=int((time.time() - t0) * 1000))


def attach_safety(output: PipelineOutput, bundle: ClaimBundle, client: SafetyClient,
                  index: dict[str, dict[str, Clause]]) -> PipelineOutput:
    return output.model_copy(update={"safety": safety_check(output, bundle, client, index)})
