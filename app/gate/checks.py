"""程式側 Gate:對 Adjudication Agent 的判定做結構化檢核與分流。

三層檢核(SPEC §5.2;LLM 只判事實,分流由查表決定):
1. 引用存在性:citation 的 policy_id+clause_no 反查條款索引必須存在;
   quote 必須為該條原文之連續子字串(空白正規化後比對)
2. 金額複核:確定性可算的約束——deny/manual 金額必為 0;approve 金額
   不得超過該保單可給付理論上界(POL-A/B 定額上界;POL-C 限額總和上界)
3. 信心分流:政策查表(門檻為常數,調整屬 prompt/guardrail 生產變更紀律)

任何檢核未過 → 強制改派 manual_review,原判定與旗標完整保留於 GateResult。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.datagen.variants import POLICY_PARAMS
from app.gate.calc import cap_group_clause, compute_line
from app.gate.policy_index import (
    Clause,
    clause_segments,
    load_policy_index,
    normalize_clause_no,
    normalize_policy_id,
    normalize_segment,
)
from app.gate.reference import POLC_SURGERY_CAP, check_line, is_full_surgery_line
from app.pipeline.derived import INJURY_ICD_PREFIXES, derive_facts
from app.schemas.claim import (
    AdjudicationResult,
    CalcKind,
    CalcLine,
    ClaimBundle,
    ClaimDecision,
    ExtractedField,
    GateFlag,
    GateResult,
    PolicyCitation,
)

# --- 分流政策(查表;修改視同生產變更) ---
CONFIDENCE_THRESHOLD = 0.70  # 判定信心低於此 → 人工(Adjudication prompt 規則 15 引用)
# 抽取欄位信心低於此 → 列入 low_confidence_fields(Extraction prompt 規則 5 引用)
# 2026-10-01 定案 0.65(原 0.80):72 案三輪格搜 t ≤ 0.65 為平坦高原(acc 0.542 / 危險錯 1 / 放行金額 18/18),
# 0.80 的「安全」來自把 48/72 案轉人工順帶關住危險錯;危險錯改由等待期/時效/結構等確定性檢核承擔。
# DI 自訂模型信心叢集於 0.65~0.80(treatment_summary / icu_days),0.65 以下才是真爛抽取(本批最低 0.49 issue_date)
EXTRACTION_FIELD_CONFIDENCE_THRESHOLD = 0.65
# 疾病等待期(三保單皆 30 日:POL-A 第22條 / POL-B 第20條 / POL-C 第23條;傷害不受限)
# 2026-10-01 72 案首跑:模型對等待期未滿案照算金額核付,程式側以生效日 vs 事件日確定性兜底(SPEC 2026-10-01-b A2)
WAITING_PERIOD_DAYS = 30

# 各保單單案可給付「理論上界」= 案基底 + 每段住院加項 × 段數(超過即為計算錯誤,細部複核屬 Phase 2 擴充)
# 定位:sanity check,不是嚴格上界(SPEC §14 2026-09-08 決策);數字與 app/datagen/variants.POLICY_PARAMS
# 之推導由 tests/test_data_invariants.py 鎖定,兩處不得漂移
# POL-A 基底:年度累計 180 日全程加護 (4000+1000)×180(第13條 90/180 日限制含 ICU 日數——ICU 日依第8條計入第7條日數);
#        每段:手術最高 50000(第10條每次住院取最高一項)+ 門診 500×6(第9條每次住院上限 6 次)
# POL-B 基底 0;每段:同次麻醉兩項皆五級 100000 + 次高 50%=50000 + 慰問 1000×30(第9、12條)
# POL-C 基底 0;每段:條款無日數上限,病房 3000/日以「90 日」為啟發式假設 + 手術 80000 + 雜費 60000 + 救護車 2000
AMOUNT_UPPER_BOUND_BASE = {"POL-A": Decimal("900000"), "POL-B": Decimal("0"), "POL-C": Decimal("0")}
AMOUNT_UPPER_BOUND_PER_ADMISSION = {"POL-A": Decimal("53000"), "POL-B": Decimal("180000"), "POL-C": Decimal("412000")}


def amount_upper_bound(code: str, n_admissions: int) -> Decimal:
    """單案理論上界;n_admissions 取 max(1, 診斷書 admissions 段數)。"""
    return AMOUNT_UPPER_BOUND_BASE[code] + AMOUNT_UPPER_BOUND_PER_ADMISSION[code] * max(1, n_admissions)


# SPEC-ae ④:數值相同之大寫數字異體(v13 C001「壹千元」對原文「壹仟元」);只收數值完全相同者,不收語意近義字(固定解法 30)
_NUMERAL_VARIANTS = str.maketrans({"仟": "千"})

def _normalize(s: str) -> str:
    """引文比對用正規化:NFKC(全半形統一)後去除空白、標點與 markdown 標記,只留字母/數字/CJK。

    2026-10-01 72 案首跑:63 筆 quote 不符中 46 筆僅差全半形標點、句尾「。/,」或條文的 **粗體** 標記;
    去標點不會讓改寫句或非連續片段矇混(文字序列仍須連續相符)。
    """
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", s)).translate(_NUMERAL_VARIANTS)


# SPEC 2026-10-01-e:引文片段規則。分隔符只留「明示省略」:省略號、表格管線、換行、句號。
# 刻意**不含「、」與「:」**——RED 實測含「:」會讓「第二級手術:新臺幣伍萬元」(等級/金額重組)矇混;
# 片段最短 2 字且**全數**比對(原訂丟棄 <4 字片段,會讓「…壹萬元…伍萬元…貳萬伍仟元」錯序漏網)
_FRAGMENT_SEPARATORS = re.compile(r"(?:\.\.\.|…+|[。|\n])")
_MIN_FRAGMENT_CHARS = 2
NON_ROUTING_FLAGS = frozenset({"citation_clause_misattributed"})  # 記錄但不改派人工
# SPEC-p P2:程式可確定性成立的拒付事由 → final_decision 改派 deny(優先於人工);ADR-008 修訂 ADR-006 之「任一旗標即人工」
DENY_FLAGS = frozenset({"waiting_period_unmet", "claim_deadline_exceeded"})


# SPEC 2026-10-02-h:引文安全版規則(v6 16 筆不符:跳列舉標記 6 / 表格「第N級手術:項目」6 / 真改寫 4)。
# R1 列舉標記(一、/(一)/1.,限句首或「:。;」之後)視為片段邊界並自兩側剝除,片段內容仍須逐字、依序;
# R2 表格列範圍:引文「第N級(手術):項目、…」的每個項目都必須落在原文該級那一列,跨列/混入金額/空項目皆不符。
# 表格儲存格以哨兵隔開:去標點後列尾項目會黏到下一列等級(「扁桃腺切除術第二級」),跨格片段不得相符。
_ENUM_MARKER = re.compile(
    r"(?:^|(?<=[:：。;；\n]))\s*(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\(?[0-9]{1,2}[.)、])")
_TABLE_ROW = re.compile(r"^\|\s*(第[一二三四五六七八九十]+級)\s*\|\s*(.+?)\s*\|\s*$", re.M)
_GRADE_QUOTE = re.compile(r"^\s*(第[一二三四五六七八九十]+級)\s*(?:手術)?\s*[:：]\s*(.+?)\s*$", re.S)
_ITEM_SPLIT = re.compile(r"[、,,。;;]")
_ELLIPSIS = re.compile(r"(?:\.\.\.|…+)")
_CELL_SENTINEL = "\x00"  # 正規化後只剩字母/數字/CJK,哨兵不可能出現在引文片段中
_NONWORD = re.compile(r"[\W_]+")


def _normalize_cells(clause_text: str) -> str:
    """原文正規化,但表格儲存格之間保留哨兵;引文只能在自身有標點/空白處跨格(見 _find)。"""
    cells = _CELL_SENTINEL.join(_normalize(cell) for cell in clause_text.split("|"))
    return re.sub(f"{_CELL_SENTINEL}+", _CELL_SENTINEL, cells)  # 列尾/換行/列首/表頭分隔列併為一個格界


def _find(raw_fragment: str, text: str, pos: int = 0) -> int:
    """在哨兵化原文中找引文片段,回傳結尾位置(找不到 -1)。引文的標點/空白處允許跨一個格界,
    無分隔的黏接(「扁桃腺切除術第二級」)不得跨格;v3/v4 實證誠實引文會以空白、「|」、「-」保留格界。"""
    words = [w for w in _NONWORD.split(unicodedata.normalize("NFKC", raw_fragment).translate(_NUMERAL_VARIANTS)) if w]
    m = re.compile(f"{_CELL_SENTINEL}?".join(map(re.escape, words))).search(text, pos)
    return -1 if m is None else m.end()


def _quote_fragments(quote: str) -> list[str]:
    return [x for x in _FRAGMENT_SEPARATORS.split(quote) if len(_normalize(x)) >= _MIN_FRAGMENT_CHARS]


def _fragments_in_order(frags: list[str], text: str) -> bool:
    pos = 0
    for f in frags:
        pos = _find(f, text, pos)
        if pos < 0:
            return False
    return True


def table_rows(clause_text: str) -> dict[str, set[str]]:
    """附錄表格 `| 第N級 | 項目、項目 |` → {等級: {正規化項目}};非表格條款回空 dict(R2 不啟用)。"""
    return {grade: {_normalize(x) for x in _ITEM_SPLIT.split(items) if _normalize(x)}
            for grade, items in _TABLE_ROW.findall(clause_text)}


def _table_row_quote_matches(quote: str, rows: dict[str, set[str]]) -> bool:
    m = _GRADE_QUOTE.match(quote)
    if not rows or m is None or m.group(1) not in rows:
        return False
    items = [x for x in (_normalize(x) for x in _ITEM_SPLIT.split(_ELLIPSIS.sub("", m.group(2)))) if x]
    return bool(items) and all(x in rows[m.group(1)] for x in items)


def quote_matches(quote: str, clause_text: str) -> bool:
    """整段連續相符,或(v3 實證模型會用省略號跨段、貼 markdown 表格列、以句號拼接多句)
    以分隔符切片後各片段**依序**逐字出現於原文;再退而以列舉標記為邊界(R1)或表格列範圍(R2)。
    捏造金額/日數、等級金額重組、錯序拼接、漏項、跨列皆不相符。"""
    t = _normalize_cells(clause_text)
    if not _normalize(quote) or _find(quote, t) >= 0:
        return True
    frags = _quote_fragments(quote)
    if frags and _fragments_in_order(frags, t):
        return True
    frags = _quote_fragments(_ENUM_MARKER.sub("\n", quote))
    if frags and _fragments_in_order(frags, _normalize_cells(_ENUM_MARKER.sub("\n", clause_text))):
        return True
    return _table_row_quote_matches(quote, table_rows(clause_text))


def validate_citation(c: PolicyCitation, index: dict[str, dict[str, Clause]]) -> GateFlag | None:
    """單筆引用檢核:保單存在 → 條號(正規化後)存在 → 引文相符(連續或有序片段)。
    引文逐字在同保單**他條** → citation_clause_misattributed(有據、條號錯;不改派,eval precision 仍計為無效)。"""
    policy = index.get(normalize_policy_id(c.policy_id))
    if policy is None:
        return GateFlag(check="citation_unknown_policy", detail=f"保單 {c.policy_id} 不存在")
    clause = policy.get(normalize_clause_no(c.clause_no))
    if clause is None:
        return GateFlag(check="citation_not_found", detail=f"{c.policy_id} {c.clause_no} 不存在")
    if c.segment.strip():
        if normalize_segment(c.segment) not in clause_segments(clause):
            return GateFlag(check="citation_segment_not_found",
                            detail=f"{c.policy_id} {c.clause_no} 無段落「{c.segment}」")
    elif not c.quote.strip():
        return GateFlag(check="citation_segment_not_found", detail=f"{c.policy_id} {c.clause_no} 未指段落且無引文")
    if not quote_matches(c.quote, clause.text):
        own = normalize_clause_no(c.clause_no)
        other = next((cn for cn, cl in policy.items() if cn != own and _find(c.quote, _normalize_cells(cl.text)) >= 0),
                     None)
        if other is not None:
            return GateFlag(check="citation_clause_misattributed",
                            detail=f"{c.policy_id} {c.clause_no} 引文實為 {other} 原文:{c.quote[:30]}…")
        return GateFlag(
            check="citation_quote_mismatch",
            detail=f"{c.policy_id} {c.clause_no} 引文與原文不符:{c.quote[:30]}…",
        )
    return None


def _check_citations(
    result: AdjudicationResult, index: dict[str, dict[str, Clause]]
) -> list[GateFlag]:
    return [f for f in (validate_citation(c, index) for c in result.citations) if f is not None]


def _check_amount(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    flags = []
    if result.decision != ClaimDecision.APPROVE:
        if result.approved_amount != 0:
            flags.append(
                GateFlag(
                    check="amount_nonzero_on_nonapprove",
                    detail=f"判定 {result.decision.value} 但金額 {result.approved_amount} ≠ 0",
                )
            )
        return flags
    if result.approved_amount == 0:
        flags.append(GateFlag(check="amount_zero_on_approve", detail="判定核付但金額為 0"))
    if not result.calculation:  # B1:金額由程式自明細加總,無明細即無可稽核之算式
        flags.append(GateFlag(check="calculation_empty_on_approve", detail="核付判定未附計算明細"))
    # J1(v8 C055):病房費 capped_per_unit 數量 9 × 3,000 但 actual 填 0 → 整行歸零、少付 22,000;列了項目卻付 0 即材料缺格
    zero_lines = [ln for ln in result.calculation if compute_line(ln) == 0]
    if zero_lines:
        flags.append(GateFlag(check="calc_zero_line",
                              detail="核付明細有算出 0 的行(材料缺格或應刪行):"
                                     + "、".join(f"{ln.clause_no} {ln.item}" for ln in zero_lines)))
    if not result.citations:
        flags.append(GateFlag(check="approve_without_citation", detail="核付判定無任何條款引用"))
    policy_number = bundle.claim_application.policy_number.value
    # 保單號前綴即保單代號(POL-X-YYYY-NN);歸戶清單以上界表的 key 為準
    code = next((c for c in AMOUNT_UPPER_BOUND_BASE if policy_number.startswith(c)), None)
    if code is None:
        flags.append(GateFlag(check="unknown_policy_number", detail=f"保單號 {policy_number} 無法歸戶"))
    else:
        bound = amount_upper_bound(code, len(bundle.diagnosis_certificate.admissions))
        if result.approved_amount > bound:
            flags.append(
                GateFlag(
                    check="amount_exceeds_upper_bound",
                    detail=f"金額 {result.approved_amount} 超過 {code} 理論上界 {bound}",
                )
            )
    return flags


def _low_confidence_paths(obj: object, prefix: str = "") -> list[str]:
    """遞迴走訪 bundle,回傳信心低於 EXTRACTION_FIELD_CONFIDENCE_THRESHOLD 的欄位路徑。

    缺席 ≠ 低信心:optional 欄位為 None(文件不存在)時整段跳過,不得被當成低信心欄位降級
    (第四類文件 incident_statement 等 optional 欄位的前置守門,2026-09-08)。
    """
    if obj is None:
        return []
    if isinstance(obj, ExtractedField):
        return [prefix] if obj.confidence < EXTRACTION_FIELD_CONFIDENCE_THRESHOLD else []
    if isinstance(obj, BaseModel):
        return [
            path
            for name in type(obj).model_fields
            for path in _low_confidence_paths(getattr(obj, name), f"{prefix}.{name}" if prefix else name)
        ]
    if isinstance(obj, list):
        return [path for i, item in enumerate(obj) for path in _low_confidence_paths(item, f"{prefix}[{i}]")]
    return []


# SPEC-ag(重試 SPEC-ae③):不參與信心分流的欄位。issue_date 不被任何判定使用、DI 信心恆低(C059 17/17);
# 固定解法 31 檢查:SPEC-af 補上合併限額少付後,17 輪中已無「issue_date 為唯一防線且模型 approve 錯」之案次
ROUTING_EXEMPT_FIELDS = frozenset({"low_confidence_fields", "diagnosis_certificate.issue_date"})

def _check_routing(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """信心分流,以**程式量測**的欄位信心為準(2026-10-01):模型自列的 `low_confidence_fields` 只作對照。

    runner 從未把門檻數值注入 prompt,模型的清單是它自己的猜測(v3:65/72 案列了欄位,其中多數信心 0.72~0.80);
    分流若信模型清單,門檻常數形同虛設。程式量測 < 門檻 → low_confidence_fields;模型漏列者另記 low_confidence_unlisted。
    """
    flags = []
    low = [p for p in _low_confidence_paths(bundle) if p not in ROUTING_EXEMPT_FIELDS]
    if low:
        omitted = [p for p in low if p not in bundle.low_confidence_fields]
        flags.append(GateFlag(
            check="low_confidence_fields",
            detail=f"欄位信心低於 {EXTRACTION_FIELD_CONFIDENCE_THRESHOLD}:{', '.join(low)}"
                   + (f";模型未列:{', '.join(omitted)}" if omitted else ""),
        ))
        if omitted:
            flags.append(GateFlag(check="low_confidence_unlisted",
                                  detail=f"欄位信心低於 {EXTRACTION_FIELD_CONFIDENCE_THRESHOLD} 但模型未列入"
                                         f" low_confidence_fields:{', '.join(omitted)}"))
    if result.confidence < CONFIDENCE_THRESHOLD:
        flags.append(GateFlag(check="low_confidence",
                              detail=f"信心 {result.confidence} 低於門檻 {CONFIDENCE_THRESHOLD}"))
    return flags


def _policy_code_of(bundle: ClaimBundle) -> str | None:
    policy_number = bundle.claim_application.policy_number.value
    return next((c for c in AMOUNT_UPPER_BOUND_BASE if policy_number.startswith(c)), None)


def _check_calculation_reference(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """approve 之每行明細反查條款參考表(P2);保單無法歸戶時交由 _check_amount 的 unknown_policy_number。"""
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    if code is None:
        return []
    return [GateFlag(check=flag, detail=detail)
            for line in result.calculation for flag, detail in check_line(code, line)]


# P3:日數型條款(quantity 必須落在 derived_facts 的候選日數集);每保單之「同一次住院」日數上限
DAY_CLAUSES: dict[str, dict[str, str]] = {
    "POL-A": {"第7條": "ward", "第8條": "icu", "第11條": "all"},
    "POL-B": {"第12條": "all"},
    "POL-C": {"第8條": "all"},
}
PER_STAY_DAY_CAP: dict[str, int | None] = {"POL-A": 90, "POL-B": 30, "POL-C": None}
# P4-1:同一次住院認定之再住院間隔(POL-A 第12條 / POL-C 第13條「出院後十四日內」含第 14 日;POL-B 無合併條款)
MERGE_GAP_DAYS: dict[str, int | None] = {"POL-A": 14, "POL-B": None, "POL-C": 14}
ANNUAL_CAP_DAYS: dict[str, int] = {"POL-A": 180}  # 第13條同一保單年度累計
SURGERY_LUMP_CLAUSES: dict[str, set[str]] = {"POL-A": {"第10條", "附錄一"}, "POL-B": {"第7條", "附錄一"}}


def _day_candidates(code: str, bundle: ClaimBundle, role: str) -> set[int]:
    """候選日數集(SPEC-g G1):以 derived_facts.stay_groups 為準——
    {各組合計截上限} ∪ {Σ 各組截上限} ∪ {各段日數};ward = 候選 − ICU;icu = ICU 日數。
    POL-B 無合併條款:每段自成一組。合併與否由間隔 ≤14 日決定(程式假設同一診斷書 = 同一疾病)。"""
    facts = derive_facts(bundle)
    days = [a.days_inclusive for a in facts.admissions if a.days_inclusive is not None]
    if not days:
        return set()
    cap = PER_STAY_DAY_CAP[code]

    def clip(d: int) -> int:
        return min(d, cap) if cap is not None else d

    group_days = facts.stay_group_days if MERGE_GAP_DAYS[code] is not None else days
    total = sum(clip(g) for g in group_days)
    counted = {clip(g) for g in group_days} | {total} | {clip(d) for d in days}
    annual = ANNUAL_CAP_DAYS.get(code)
    if annual is not None:  # 年度累計上限(POL-A 第13條後段):多段合計以單行 180 表達亦合法(C061 真值形狀)
        counted.add(min(total, annual))
    icus = {a.icu_days for a in facts.admissions} | {facts.total_icu_days}
    if role == "icu":
        return icus
    if role == "ward":
        return counted | {c - i for c in counted for i in icus if c - i >= 0}
    return counted


def _check_calculation_days(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    if code is None:
        return []
    flags = []
    for line in result.calculation:
        role = DAY_CLAUSES[code].get(normalize_clause_no(line.clause_no))
        if role is None or line.kind not in (CalcKind.PER_UNIT, CalcKind.CAPPED_PER_UNIT):
            continue
        cands = _day_candidates(code, bundle, role)
        if cands and line.quantity not in cands:
            flags.append(GateFlag(check="calc_days_mismatch",
                                  detail=f"{code} {line.clause_no}「{line.item}」日數 {line.quantity} 不在程式算出的"
                                         f"候選集 {sorted(cands)}(含入出院日;各段/合計/上限截斷)"))
    return flags


SPECIAL_WAITING_DAYS: dict[str, int] = {"POL-A": 90}  # 第23條特定疾病(附錄二)延長等待期;POL-B/C 無此條款
CLAIM_DEADLINE_YEARS = 2  # 給付申請之時效:POL-A 第14條 / POL-B 第29條時效章 / POL-C 第15條「自得為申請之日起經過二年」


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 2 月 29 日起算 → 到期日退至 2 月 28 日
        return d.replace(year=d.year + years, day=28)


# SPEC-ag:POL-A 附錄二特定疾病 → ICD-10 範圍(使用者確認 2026-10-07);(字母, 起, 迄) 以「字母後數字前綴」比較
_APPENDIX2_ICD: tuple[tuple[str, str, str], ...] = (
    ("C", "00", "97"),        # 1 惡性腫瘤
    ("I", "20", "25"),        # 2 缺血性心臟病
    ("I", "60", "69"),        # 3 腦血管疾病
    ("K", "703", "703"),      # 4 肝硬化(酒精性)
    ("K", "743", "746"),      # 4 肝硬化(其他)
    ("N", "183", "186"),      # 5 慢性腎臟病第三期以上
    ("E", "08", "13"),        # 6 糖尿病及其併發症
)
_ICD_CODE = re.compile(r"^([A-Z])(\d{2,})$")


def appendix2_match(icd_field: str) -> bool | None:
    """任一 ICD 碼(以 ; , 空白分隔)落在附錄二範圍 → True;皆可解析且皆不在 → False;無可解析碼 → None(保守提醒)。"""
    codes = [m for m in (_ICD_CODE.match(c.replace(".", "").strip().upper())
                         for c in re.split(r"[;,;,\s]+", icd_field or "")) if m]
    if not codes:
        return None
    for m in codes:
        letter, digits = m.group(1), m.group(2)
        for lt, lo, hi in _APPENDIX2_ICD:
            if letter == lt and lo <= digits[:len(lo)] <= hi:
                return True
    return False


def _check_special_waiting_period(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """P4-3 提醒型:POL-A、approve、疾病、WAITING_PERIOD_DAYS ≤ 距生效日 < 90 → special_waiting_period_review。

    附錄二特定疾病需讀診斷名稱(語意),程式不判;只把落在延長等待期窗口內的疾病核付交人工確認。
    v3 實證:擋下 C022(71 日,附錄二)、C049(70 日);代價 C061(44 日,正確核付)轉人工。<30 由 A2 負責。
    """
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    special = SPECIAL_WAITING_DAYS.get(code or "")
    if special is None:
        return []
    facts = derive_facts(bundle)
    d = facts.days_since_effective
    if facts.is_injury_icd or d is None or not (WAITING_PERIOD_DAYS <= d < special):
        return []
    if appendix2_match(bundle.diagnosis_certificate.icd_code.value) is False:  # SPEC-ag:ICD 明確不在附錄二
        return []
    return [GateFlag(check="special_waiting_period_review",
                     detail=f"{code} 距生效日 {d} 日,落在第23條特定疾病 {special} 日延長等待期窗口內"
                            f"(ICD {bundle.diagnosis_certificate.icd_code.value or '未填'}),需人工確認是否屬附錄二")]


# F3(SPEC-f):decision=approve 但 reasoning 寫出拒付語(v4 C046:「本案應拒付」卻 approve、信心 0.95)
# 用詞來源:adjudication prompt 規則 0/3 的「deny」措辭與條款「不負給付保險金之責任」;否定前綴(不/無/非/未)不命中
_DENIAL_PHRASES = re.compile(
    r"(?<![不無非未])(應予?拒付|予以拒付|本案拒付|應拒絕給付|不負給付(?:保險金)?之?責任|不予給付|不符合給付)"
)


def _check_decision_reasoning(result: AdjudicationResult) -> list[GateFlag]:
    if result.decision is not ClaimDecision.APPROVE:
        return []
    hits = [m.group(0) for m in _DENIAL_PHRASES.finditer(result.reasoning)]
    # 排除「不負給付責任之情形不存在」這類後綴否定:命中片段後 12 字內出現「不存在/不成立/不適用」
    def _negated_after(h: str) -> bool:
        i = result.reasoning.find(h)
        return re.search(r"(不存在|不成立|不適用|不觸發)", result.reasoning[i:i + len(h) + 12]) is not None

    real = [h for h in hits if not _negated_after(h)]
    if not real:
        return []
    return [GateFlag(check="decision_reasoning_conflict",
                     detail=f"判定 approve 但理由含拒付語:{'、'.join(dict.fromkeys(real))}")]


# ---- SPEC 2026-10-02-g 提醒型檢核(關鍵字/字串含;只轉人工,不判對錯;漏接方向為「不旗標」)----
_NON_NHI = re.compile(r"非健保|非全民健康保險|自費身分|非特約")
# SPEC-u B 擴充:「既往」「定期/長期…追蹤」「確診於 N 年前」「N 年前」(C024 summary「確診於四年前,定期…追蹤」);
# 69 份合成 bundle 只有 C024(deny)新命中,approve 側 G5 零變動(tests/test_spec_u.py U2-5 釘住)
_PRE_EXISTING = re.compile(r"病史|長期.{0,6}追蹤|自\s?\d{4}[-/年].{0,4}起.{0,12}追蹤|投保前"
                           r"|既往|(?:定期|長期).{0,10}追蹤|確診於.{0,6}年前|\d+\s*年前")
# 各保單既往症條款(條款檔「(既往症)」標題;U2-6 測試比對):deny 引用此條卻無源頭證據 → 人工
PRE_EXISTING_CLAUSE: dict[str, str] = {"POL-A": "第18條", "POL-B": "第16條", "POL-C": "第19條"}
# SPEC-ai:手術定義含「單純之診斷性處置不視為手術」排除者(僅 POL-B 第2條第三款)
SURGERY_DEFINITION_CLAUSE: dict[str, str] = {"POL-B": "第2條"}
_ROOM_DAYS = re.compile(r"(\d+)\s*日")
RECEIPT_CATEGORY_KEYWORDS = (  # POL-C 第8~11條給付項目 + 第10條負面列舉;「雜費」為真值收據常見總稱
    "病房", "膳食", "手術", "麻醉", "藥", "醫材", "衛材", "檢查", "檢驗", "放射", "注射", "輸血", "血液",
    "復健", "救護車", "雜費", "診斷證明", "病歷", "掛號", "電話", "電視", "伙食", "看護",
)

# SPEC-q:收據類別 → 應有之明細條號(POL-C 第8~11條);負面列舉(診斷證明/病歷/掛號/電話/電視/伙食/看護)不列
RECEIPT_CLAUSE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "第8條": ("病房", "膳食"),
    "第9條": ("手術", "麻醉"),
    "第10條": ("藥", "醫材", "衛材", "檢查", "檢驗", "放射", "注射", "輸血", "血液", "復健", "雜費"),
    "第11條": ("救護車",),
}
# 第9條手術費亦可由附錄一(手術等級表)之明細行覆蓋(v6 C033 / v7 C035 實證)
COVERING_CLAUSES: dict[str, frozenset[str]] = {c: frozenset({c}) for c in RECEIPT_CLAUSE_CATEGORIES}
COVERING_CLAUSES["第9條"] = frozenset({"第9條", "附錄一"})


def receipt_clauses_hit(item_names: list[str]) -> dict[str, list[str]]:
    """每個給付類別 → 命中的收據項目名(依類別表順序;一項目可命中多類別)。"""
    hit: dict[str, list[str]] = {}
    for clause, kws in RECEIPT_CLAUSE_CATEGORIES.items():
        names = [n for n in item_names if any(k in n for k in kws)]
        if names:
            hit[clause] = names
    return hit


def _check_receipt_coverage(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-q(v7 C069):POL-C approve 時,收據命中的給付類別必須有對應條號之明細行;
    缺行 = 少付卻放行的「材料缺格」洞(cap-sum 檢核在合併拆行後失效,此檢核直接看收據)。只旗標改派。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":  # SPEC-q 只檢 POL-C
        return []
    present = {normalize_clause_no(ln.clause_no) for ln in result.calculation}
    hit = receipt_clauses_hit([it.item_name for it in bundle.medical_receipt.items])
    missing = [(clause, names) for clause, names in hit.items() if not (COVERING_CLAUSES[clause] & present)]
    if not missing:
        return []
    detail = ";".join(f"{clause} ← {'、'.join(names)}" for clause, names in missing)
    return [GateFlag(check="receipt_category_uncovered",
                     detail=f"收據項目命中之給付類別無對應明細行:{detail}")]



def _check_deduction_without_copy(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-y(C073):POL-C 第14條之扣除以「收據正本已用於申領其他保險給付」為要件;收據為正本(is_copy=False)
    時任何明細行帶扣除額即屬無據扣除(日額型他保誤扣 14,000 → 64,000 為合法值但非本案值,固定解法 25)。只旗標改派。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":
        return []
    if bundle.medical_receipt.is_copy:
        return []
    bad = [f"{ln.clause_no} {ln.deduction}" for ln in result.calculation if ln.deduction > 0]
    if not bad:
        return []
    return [GateFlag(check="deduction_without_copy",
                     detail=f"收據為正本,第14條扣除要件(收據正本已用於他保)不成立,明細不得扣除:{'、'.join(bad)}")]


# SPEC-z:POL-C 他保四桶 ↔ 給付條款(第14條「於各項限額內」逐桶扣除)
DEDUCTION_BUCKET_CLAUSE: dict[str, str] = {"room_fee": "第8條", "surgery_fee": "第9條",
                                           "misc_fee": "第10條", "ambulance_fee": "第11條"}


def _check_deduction_bucket(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-z(v12 C072):收據影本時,各條扣除額合計須等於申請書對應桶之他保金額;扣錯桶時總額不變、
    結果仍在限額內即放行(+10,000)。`unallocated` 不比對(現行 prompt 影本 + 無法歸桶即轉人工)。只旗標改派。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":
        return []
    if not bundle.medical_receipt.is_copy:
        return []
    paid = bundle.claim_application.other_insurer_paid
    got: dict[str, Decimal] = {}
    for ln in result.calculation:
        clause = normalize_clause_no(ln.clause_no)
        got[clause] = got.get(clause, Decimal(0)) + ln.deduction
    bad = [f"{clause} 應扣 {getattr(paid, bucket)} 實扣 {got.get(clause, Decimal(0))}"
           for bucket, clause in DEDUCTION_BUCKET_CLAUSE.items()
           if got.get(clause, Decimal(0)) != getattr(paid, bucket)]
    if not bad:
        return []
    return [GateFlag(check="deduction_bucket_mismatch",
                     detail=f"收據影本,他保扣除須逐桶對應申請書(第14條):{'、'.join(bad)}")]


def _check_deduction_unallocated(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-ad:收據影本且他保有無法歸入四桶之給付(unallocated > 0)→ 人工(原僅 prompt 規則 9a 守住;
    SPEC-z 落桶檢核不比對 unallocated,approve 會直接放行)。收據正本不檢(C073:不扣、不轉人工)。只旗標改派。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":
        return []
    unallocated = bundle.claim_application.other_insurer_paid.unallocated
    if not bundle.medical_receipt.is_copy or unallocated <= 0:
        return []
    return [GateFlag(check="deduction_unallocated",
                     detail=f"收據影本,他保給付 {unallocated} 無法歸入四桶(第14條逐桶扣除無從認定),需人工核定")]


def _check_receipt_amount_coverage(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-u A′(cloud1 C036):POL-C approve 且有第10條 capped_actual 行時,各行 actual(ratio 前)之和
    不得同時低於「收據第10條類別項目合計」與「cap × 同一次住院組數」——兩者皆低 = 同類別內漏項卻放行
    (藥費 12,200 漏、只填檢驗費 6,500);限額已綁住者為合法少付。只旗標改派;無第10條行交 SPEC-q 類別檢核。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":  # SPEC-u 只檢 POL-C
        return []
    lines = [ln for ln in result.calculation
             if ln.kind is CalcKind.CAPPED_ACTUAL and normalize_clause_no(ln.clause_no) == "第10條"]
    names = receipt_clauses_hit([it.item_name for it in bundle.medical_receipt.items]).get("第10條")
    if not lines or not names:
        return []
    receipt_total = sum((it.amount for it in bundle.medical_receipt.items if it.item_name in names), Decimal(0))
    actual_total = sum((ln.actual_amount for ln in lines), Decimal(0))
    cap_total = max(ln.cap for ln in lines) * max(1, len(derive_facts(bundle).stay_groups))
    if actual_total >= receipt_total or actual_total >= cap_total:
        return []
    return [GateFlag(check="receipt_amount_uncovered",
                     detail=f"第10條明細 actual 合計 {actual_total} 低於收據同類別項目合計 {receipt_total}"
                            f"({'、'.join(names)}),且未達限額 {cap_total}:疑漏項")]


def _check_pre_existing_deny(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-u B(cloud1 C066,歷次首件危險錯):deny 引用該保單既往症條款,但診斷書治療摘要/診斷
    皆無既往證據(病史/追蹤/投保前/N 年前…)→ 改派人工,不改方向。理由關鍵字不可用(模型固定寫
    「③既往症:未見記載」檢核句,幾乎全部 deny 命中),故以「引用條號」為觸發。"""
    if result.decision is not ClaimDecision.DENY:
        return []
    code = _policy_code_of(bundle)
    clause = PRE_EXISTING_CLAUSE.get(code or "")
    if clause is None or clause not in {normalize_clause_no(c.clause_no) for c in result.citations}:
        return []
    dc = bundle.diagnosis_certificate
    if _PRE_EXISTING.search(dc.treatment_summary.value) or _PRE_EXISTING.search(dc.diagnosis.value):
        return []
    return [GateFlag(check="pre_existing_unsupported",
                     detail=f"拒付引用 {clause}(既往症),但診斷書治療摘要與診斷均無病史/追蹤/投保前等既往記載,"
                            f"需人工確認拒付依據")]


def _check_surgery_definition_deny(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-ai(C031):deny 引用手術定義條款(POL-B 第2條「單純之診斷性處置」排除)→ 是否構成手術屬認定爭議,
    改派人工。18 輪此形狀 5 次真值皆人工(C031×3、C007×2);不以摘要「切除」等字樣觸發,否則漏 C007(胃鏡止血)。"""
    if result.decision is not ClaimDecision.DENY:
        return []
    clause = SURGERY_DEFINITION_CLAUSE.get(_policy_code_of(bundle) or "")
    if clause is None or clause not in {normalize_clause_no(c.clause_no) for c in result.citations}:
        return []
    return [GateFlag(check="surgery_definition_deny",
                     detail=f"拒付引用 {clause}(手術定義:單純之診斷性處置不視為手術),處置是否構成手術需人工認定")]


# SPEC-al:POL-C 第12條「依第八條至第十一條約定計算之金額,按百分之六十五給付」→ 同案限額行比例必一致
_POLC_RATIO_GROUPS = frozenset({"第8條", "第9條", "第10條", "第11條"})


def _check_ratio_consistency(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-al:POL-C 限額行(第8~11條,附錄一手術行經 cap_group_clause 歸第9條)ratio 不一致 → 人工。

    2026-10-10 C035:病房 1、手術 / 雜費 0.65 放行 +11,550;G2 只檢「無任何 0.65」故漏接。POL-B 次高 50% 為合法混用,不檢;
    多段住院分屬特約 / 非特約醫院理論上可合法混用,一律人工(往安全方向)。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":
        return []
    capped = (CalcKind.CAPPED_ACTUAL, CalcKind.CAPPED_PER_UNIT)
    ratios = {ln.ratio for ln in result.calculation if ln.kind in capped and cap_group_clause(ln) in _POLC_RATIO_GROUPS}
    if len(ratios) < 2:
        return []
    return [GateFlag(check="calc_ratio_inconsistent",
                     detail=f"POL-C 限額明細行比例不一致 {sorted(str(r) for r in ratios)}"
                            "(第12條:非健保身分時第8~11條全部按 65% 給付)")]


def _bundle_text(bundle: ClaimBundle) -> str:
    dc, rc = bundle.diagnosis_certificate, bundle.medical_receipt
    return "|".join([dc.treatment_summary.value, dc.hospital_name.value, rc.hospital_name.value, dc.diagnosis.value])


def _check_reviews(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    if code is None:
        return []
    flags: list[GateFlag] = []
    text = _bundle_text(bundle)
    # G2 非健保 65%
    if code == "POL-C" and _NON_NHI.search(text) and not any(ln.ratio == Decimal("0.65") for ln in result.calculation):
        flags.append(GateFlag(check="nhi_ratio_review",
                              detail="文件含非健保/自費身分字樣,但無任何 ratio 0.65 之明細行(第12條按 65% 給付)"))
    # G3 收據病房費日數
    facts = derive_facts(bundle)
    legal_days = (set(facts.stay_group_days) | {a.days_inclusive for a in facts.admissions if a.days_inclusive}
                  | {facts.total_days})
    for it in bundle.medical_receipt.items:
        m = _ROOM_DAYS.search(it.item_name)
        if "病房" in it.item_name and m and int(m.group(1)) not in legal_days:
            flags.append(GateFlag(check="receipt_days_mismatch",
                                  detail=f"收據「{it.item_name}」日數 {m.group(1)} 與診斷書住院日數 "
                                         f"{sorted(legal_days)} 不符"))
    # G4 POL-C 收據項目分類(第26條分類爭議)
    if code == "POL-C":
        unknown = [it.item_name for it in bundle.medical_receipt.items
                   if not any(k in it.item_name for k in RECEIPT_CATEGORY_KEYWORDS)]
        if unknown:
            flags.append(GateFlag(check="receipt_item_unclassified",
                                  detail=f"收據項目無法歸入第8~11條給付項目或負面列舉:{'、'.join(unknown)}"))
    # G5 既往症提醒
    if _PRE_EXISTING.search(bundle.diagnosis_certificate.treatment_summary.value):
        flags.append(GateFlag(check="pre_existing_review",
                              detail="治療摘要含病史/長期追蹤/投保前字樣,需人工確認既往症條款"))
    # G6 未列表手術
    if code in SURGERY_LUMP_CLAUSES and any(ln.kind is CalcKind.LUMP_SUM for ln in result.calculation):
        names = _appendix_surgery_names(code)
        raw_summary = bundle.diagnosis_certificate.treatment_summary.value
        summary = re.sub(r"\s+", "", raw_summary).translate(_OCR_CONFUSABLES)
        if names and not any(n in summary for n in names):
            flags.append(GateFlag(check="unlisted_surgery_review",
                                  detail="治療摘要未含附錄一任一手術名,手術等級需人工核定(第10條/第11條未列表手術)"))
    return flags


# DI 常見異體字(v5 C063「内」):比對附錄一手術名前先正規化
_OCR_CONFUSABLES = str.maketrans({"内": "內", "爲": "為", "裏": "裡", "强": "強", "着": "著"})
_SURGERY_NAME_CACHE: dict[str, frozenset[str]] = {}


def _appendix_surgery_names(code: str) -> frozenset[str]:
    if code not in _SURGERY_NAME_CACHE:
        clause = load_policy_index().get(code, {}).get("附錄一")
        names = set(re.findall(r"[\u4e00-\u9fff]+術", clause.text)) - {"手術"} if clause else set()
        _SURGERY_NAME_CACHE[code] = frozenset(names)
    return _SURGERY_NAME_CACHE[code]


_SURGERY_GRADE_CLAUSES = {"第7條", "附錄一", "第8條", "第9條"}
_GRADE_NO = {"第一級": 1, "第二級": 2, "第三級": 3, "第四級": 4, "第五級": 5}


def _summary_surgery_lumps(code: str, summary: str) -> list[int]:
    """摘要中命中的附錄一手術名 → 各自等級定額(可重複,依手術名計數),由大到小。"""
    clause = load_policy_index()[code]["附錄一"]
    grades = POLICY_PARAMS[code]["surgery"]
    text = re.sub(r"\s+", "", summary).translate(_OCR_CONFUSABLES)
    lumps = [int(grades[_GRADE_NO[g]]) for g, items in table_rows(clause.text).items()
             if g in _GRADE_NO for n in items if n in text]
    return sorted(lumps, reverse=True)


def _check_surgery_grades(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """L1(SPEC 2026-10-02-l):POL-B 摘要命中 ≥ 2 個附錄一手術名時,每條定額行的有效金額必須屬於
    {最高等級全額} ∪ {其餘各等級 × 0.5}(第9條:最高一項全額、次高 50%)。
    route_v1 C051:膽囊(三級 30,000)+ 疝氣(二級 15,000)填 15,000 × 1,因 15,000 亦為三級折半而被參考表接受。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-B":
        return []
    lines = [ln for ln in result.calculation
             if ln.kind is CalcKind.LUMP_SUM and normalize_clause_no(ln.clause_no) in _SURGERY_GRADE_CLAUSES]
    lumps = _summary_surgery_lumps("POL-B", bundle.diagnosis_certificate.treatment_summary.value)
    if len(lumps) < 2 or not lines:
        return []
    # 多重集一對一配對:最高一項全額只能用一次、其餘各 50% 各一次(同等級兩項 15,000+15,000 第二條須為 7,500)
    pool = [Decimal(lumps[0])] + [Decimal(x) / 2 for x in lumps[1:]]
    bad = []
    for ln in sorted(lines, key=compute_line, reverse=True):
        amt = compute_line(ln)
        if amt in pool:
            pool.remove(amt)
        else:
            bad.append(f"{ln.clause_no} {amt}")
    if not bad:
        return []
    return [GateFlag(check="calc_surgery_grade_mismatch",
                     detail=f"摘要命中手術等級定額 {lumps},定額行允許 {[str(x) for x in sorted(pool, reverse=True)]}"
                            f" 外尚有不符:{'、'.join(bad)}(第9條:最高一項全額、次高 50%)")]


def _summary_surgery_grades(code: str, summary: str) -> list[int]:
    """摘要中命中的附錄一手術名 → 等級數字(可重複),由高到低。"""
    clause = load_policy_index()[code]["附錄一"]
    text = re.sub(r"\s+", "", summary).translate(_OCR_CONFUSABLES)
    return sorted((_GRADE_NO[g] for g, items in table_rows(clause.text).items()
                   if g in _GRADE_NO for n in items if n in text), reverse=True)


def _check_surgery_grade_value(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """W1/W2(SPEC-w):手術行填的是合法等級值、但不是摘要手術的等級值。
    POL-A(單段住院):第10條「二項以上以最高一項為限」→ 定額行恰一條且 = 最高等級定額(v2 C054 填 50,000 被放行)。
    POL-C:第9條限額 = 捌萬 × 最高等級百分比(v8 C035 第三級填 40,000)。
    摘要比對不到附錄一手術名 → 不檢(第9條「程度相當」由人工)。"""
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    if code == "POL-B":
        return _check_polb_single_surgery_value(result, bundle)
    if code not in ("POL-A", "POL-C"):
        return []
    grades = _summary_surgery_grades(code, bundle.diagnosis_certificate.treatment_summary.value)
    if not grades:
        return []
    if code == "POL-A":
        if len(bundle.diagnosis_certificate.admissions) > 1:
            return []
        lines = [ln for ln in result.calculation
                 if ln.kind is CalcKind.LUMP_SUM and normalize_clause_no(ln.clause_no) in SURGERY_LUMP_CLAUSES[code]]
        want = Decimal(POLICY_PARAMS[code]["surgery"][grades[0]])
        got = sorted(compute_line(ln) for ln in lines)
        if not lines or got == [want]:
            return []
        return [GateFlag(check="calc_surgery_grade_value",
                         detail=f"POL-A 摘要手術最高為第{grades[0]}級(定額 {want}),定額行為 {[str(x) for x in got]}"
                                "(第10條:二項以上以給付金額最高之一項為限)")]
    want = Decimal(POLC_SURGERY_CAP[grades[0]])
    bad = [str(ln.cap) for ln in result.calculation
           if ln.kind is CalcKind.CAPPED_ACTUAL and cap_group_clause(ln) == "第9條" and ln.cap != want]
    if not bad:
        return []
    return [GateFlag(check="calc_surgery_grade_value",
                     detail=f"POL-C 摘要手術最高為第{grades[0]}級,第9條限額應為 {want},明細填 {'、'.join(bad)}")]


# POL-B 第10條「前次手術後十四日內就同一部位再次接受手術者,視為同一次手術」
_REPEAT_SURGERY_GAP_DAYS = 14
_REPEAT_SURGERY_CLAUSES = _SURGERY_GRADE_CLAUSES | {"第10條"}


def _check_surgery_repeat(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-aa:POL-B 摘要只命中一個附錄一手術名、摘要日期相距 ≤ 14 日、手術定額行 ≥ 2 → 人工
    (v13 C047 痔瘡切除術兩次門診各 2,500 放行;同疾病 / 同部位屬醫學判斷,不由程式認定)。"""
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-B":
        return []
    summary = bundle.diagnosis_certificate.treatment_summary.value
    text = re.sub(r"\s+", "", summary).translate(_OCR_CONFUSABLES)
    names = {n for n in _appendix_surgery_names("POL-B") if n in text}
    dates = sorted({d for d in (_parse_date(x) for x in re.findall(r"\d{4}-\d{2}-\d{2}", summary)) if d})
    lines = [ln for ln in result.calculation
             if ln.kind is CalcKind.LUMP_SUM and normalize_clause_no(ln.clause_no) in _REPEAT_SURGERY_CLAUSES]
    if len(names) != 1 or len(dates) < 2 or len(lines) < 2:
        return []
    if (dates[-1] - dates[0]).days > _REPEAT_SURGERY_GAP_DAYS:
        return []
    return [GateFlag(check="surgery_repeat_within_14d",
                     detail=f"摘要手術 {next(iter(names))} 於 {dates[0]}~{dates[-1]} 間出現,手術定額行 {len(lines)} 條"
                            "(第10條:十四日內同部位再手術視為同一次,是否同疾病同部位由人工認定)")]


_POLB_FULL_CLAUSES = SURGERY_LUMP_CLAUSES["POL-B"]  # 第7條 / 附錄一:住院手術全額
_POLB_OUTPATIENT_CLAUSE = "第8條"  # 門診手術:同等級 50%


def _check_polb_single_surgery_value(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-ac:POL-B 摘要恰命中 1 個附錄一手術名 → 住院定額行 = 等級定額、門診第8條定額行 = 50%
    (v14 C029 第三級 30,000 填 60,000 被放行;≥ 2 個手術名由 L1 `calc_surgery_grade_mismatch` 負責)。"""
    grades = _summary_surgery_grades("POL-B", bundle.diagnosis_certificate.treatment_summary.value)
    if len(grades) != 1:
        return []
    full = Decimal(POLICY_PARAMS["POL-B"]["surgery"][grades[0]])
    want = {**{c: full for c in _POLB_FULL_CLAUSES}, _POLB_OUTPATIENT_CLAUSE: full / 2}
    bad = [f"{ln.clause_no} {compute_line(ln)}" for ln in result.calculation
           if ln.kind is CalcKind.LUMP_SUM and normalize_clause_no(ln.clause_no) in want
           and compute_line(ln) != want[normalize_clause_no(ln.clause_no)]]
    if not bad:
        return []
    return [GateFlag(check="calc_surgery_grade_value",
                     detail=f"POL-B 摘要手術為第{grades[0]}級(住院 {full} / 門診 {full / 2}),"
                            f"定額行為 {'、'.join(bad)}")]


# SPEC-ae ①:等待期內發病之處理(POL-A 第24條 / POL-B 第21條 / POL-C 第24條)——同句含日期與「診斷」
_PRIOR_DIAGNOSIS = re.compile(r"(\d{4}-\d{2}-\d{2})[^。;;]*?診斷")


def _check_prior_diagnosis(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """C058 / C049:等待期屆滿後住院,但摘要載明等待期內(生效後 0~29 日)已經診斷 → 人工
    (原僅靠無關旗標轉人工;同疾病 / 併發症屬醫學判斷,不由程式認定)。傷害、事件仍在等待期內者不檢。"""
    if result.decision is not ClaimDecision.APPROVE:
        return []
    facts = derive_facts(bundle)
    effective = _parse_date(bundle.claim_application.policy_effective_date.value)
    if facts.is_injury_icd or effective is None or facts.days_since_effective is None:
        return []
    if facts.days_since_effective < WAITING_PERIOD_DAYS:
        return []
    summary = bundle.diagnosis_certificate.treatment_summary.value
    hits = [d for d in (_parse_date(m) for m in _PRIOR_DIAGNOSIS.findall(summary))
            if d is not None and 0 <= (d - effective).days < WAITING_PERIOD_DAYS]
    if not hits:
        return []
    return [GateFlag(check="waiting_period_prior_diagnosis",
                     detail=f"摘要載明 {hits[0]}(生效後第 {(hits[0] - effective).days} 日,等待期內)已診斷;"
                            "等待期內發病之疾病及其併發症於屆滿後仍不給付(POL-A/C 第24條、POL-B 第21條),需人工確認")]


# SPEC-af:每次住院限額之條款(POL-C 第10條雜費);第8條逐日限額由 P3 分段檢核、第9條限額依手術等級不依住院段
PER_STAY_CAP_CLAUSES: dict[str, frozenset[str]] = {"POL-C": frozenset({"第10條"})}


def _check_stay_cap_merged(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """C059:多個「同一次住院組」卻只開較少的限額行,且 ratio 前合計超過「行數 × 限額」——
    一個限額吃掉多次住院而少付(cap-sum 只抓超付)。只旗標改派。"""
    if result.decision is not ClaimDecision.APPROVE:
        return []
    clauses = PER_STAY_CAP_CLAUSES.get(_policy_code_of(bundle) or "", frozenset())
    groups = len(derive_facts(bundle).stay_groups)
    flags = []
    for clause in sorted(clauses):
        lines = [ln for ln in result.calculation
                 if ln.kind is CalcKind.CAPPED_ACTUAL and normalize_clause_no(ln.clause_no) == clause]
        if not lines or len(lines) >= groups:
            continue
        total = sum((max(ln.actual_amount - ln.deduction, Decimal(0)) for ln in lines), Decimal(0))
        cap = max(ln.cap for ln in lines)
        if total > cap * len(lines):
            flags.append(GateFlag(check="calc_stay_cap_merged",
                                  detail=f"{clause} 有 {groups} 個同一次住院組但只列 {len(lines)} 行限額,"
                                         f"合計 {total} 超過 {len(lines)} × {cap}:多次住院合併套一個限額而少付"
                                         "(第13條逾十四日不視為同一次,限額應各自計算)"))
    return flags


def _check_stay_days_coverage(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """W3(SPEC-w):多段住院時,全日數型條款(DAY_CLAUSES 之 all)各行日數合計須涵蓋全部住院段
    (Σ 各組截上限;POL-A 另接受年度 180 上限)。單段候選值合法但只填一段 → v8 C060 60+45 只填 60。
    假設:同一診斷書上的各段住院皆在申請範圍內(已知限制)。"""
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    if code is None:
        return []
    facts = derive_facts(bundle)
    days = [a.days_inclusive for a in facts.admissions if a.days_inclusive is not None]
    if len(days) < 2:
        return []
    cap = PER_STAY_DAY_CAP[code]
    groups = facts.stay_group_days if MERGE_GAP_DAYS[code] is not None else days
    total = sum(min(g, cap) if cap is not None else g for g in groups)
    accepted = {total}
    if code in ANNUAL_CAP_DAYS:
        accepted.add(min(total, ANNUAL_CAP_DAYS[code]))
    sums: dict[str, int] = {}
    for ln in result.calculation:
        clause = normalize_clause_no(ln.clause_no)
        if DAY_CLAUSES[code].get(clause) == "all" and ln.kind in (CalcKind.PER_UNIT, CalcKind.CAPPED_PER_UNIT):
            sums[clause] = sums.get(clause, 0) + ln.quantity
    return [GateFlag(check="calc_stay_days_uncovered",
                     detail=f"{code} {clause} 各行日數合計 {q} 未涵蓋全部 {len(days)} 段住院(應為 {sorted(accepted)})")
            for clause, q in sums.items() if q not in accepted]


def _check_cap_sum(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """F1(SPEC 2026-10-02-f):同一 capped_actual 條款拆多行時,各行子項之和不得超過 cap × 住院段數。

    v4 C003:手術費 18,000 + 麻醉費 4,000 各自套 20,000 限額 → 22,000(條款為合計受限);
    合法拆行(C036 雜費兩行合計 ≤ 60,000、兩段住院各一行)不受影響。逐日限額 capped_per_unit 不適用。
    """
    if result.decision is not ClaimDecision.APPROVE:
        return []
    groups: dict[str, list[CalcLine]] = {}
    for ln in result.calculation:
        if ln.kind is CalcKind.CAPPED_ACTUAL:
            groups.setdefault(cap_group_clause(ln), []).append(ln)
    # G1:限額「每次住院」以同一次住院分組計(POL-C 第13條雜費限額合併),非段數
    segments = max(1, len(derive_facts(bundle).stay_groups))
    flags = []
    for clause, lines in groups.items():
        if len(lines) < 2:
            continue
        cap = max(ln.cap for ln in lines)
        # SPEC-i(v7 C069):以 ratio 前金額比較——65% 比例會把 22,000 遮成 14,300 而漏接超限
        total = sum((max(ln.actual_amount - ln.deduction, Decimal(0)) for ln in lines), Decimal(0))
        if total > cap * segments:
            flags.append(GateFlag(check="calc_cap_sum_exceeded",
                                  detail=f"{clause} 拆 {len(lines)} 行合計 {total} 超過限額 {cap} × 同一次住院組數 "
                                         f"{segments}(限額為合計受限;十四日內再住院視為同一次)"))
    return flags


ROOM_ITEM_KEYWORD = "病房"


def _check_segment_cap(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """SPEC-p P3(R2 C055 實證):POL-C 第8條病房費為逐日限額且各段住院獨立——
    期望值 = Σ_i min(病房費_i, 日額 × 該段天數);LLM 以合計日數 × 日額套一次(27,000)會多付。
    只在「住院段數 == 含『病房』之收據項目數 ≥ 2」且各段天數可解析時啟用;只旗標不改金額。
    """
    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":
        return []
    lines = [ln for ln in result.calculation
             if normalize_clause_no(ln.clause_no) == "第8條" and ln.kind is CalcKind.CAPPED_PER_UNIT]
    if not lines:
        return []
    facts = derive_facts(bundle)
    days = [a.days_inclusive for a in facts.admissions]
    rooms = [it.amount for it in bundle.medical_receipt.items if ROOM_ITEM_KEYWORD in it.item_name]
    if len(days) < 2 or len(days) != len(rooms) or any(d is None for d in days):
        return []
    unit = Decimal(POLICY_PARAMS["POL-C"]["room_daily_cap"])
    expected = sum((min(room, unit * Decimal(d or 0)) for room, d in zip(rooms, days, strict=True)), Decimal(0))
    got = sum((compute_line(ln) for ln in lines), Decimal(0))
    if got != expected:
        return [GateFlag(check="calc_segment_cap_mismatch",
                         detail=f"POL-C 第8條病房費逐日限額應各段獨立:Σ min(病房費, {unit}×天數) = {expected},"
                                f"明細算得 {got}(段天數 {days}、病房費 {[str(r) for r in rooms]})")]
    return []


def _check_claim_deadline(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """P4-2:approve 且申請日晚於「得為申請之日 + 二年」→ claim_deadline_exceeded(v3 C067:752 日仍核付)。"""
    if result.decision is not ClaimDecision.APPROVE:
        return []
    facts = derive_facts(bundle)
    event = _parse_date(facts.claim_event_date)
    applied = _parse_date(facts.application_date)
    if event is None or applied is None:
        return []
    deadline = _add_years(event, CLAIM_DEADLINE_YEARS)
    if applied > deadline:
        return [GateFlag(check="claim_deadline_exceeded",
                         detail=f"得為申請之日 {event} 起二年至 {deadline},申請日 {applied} 已逾"
                                f"({facts.days_event_to_application} 日);給付申請權時效消滅")]
    return []


def _check_calculation_structure(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """P4-1 結構檢核(approve):POL-A 第7/8條 ⇔ 第11條配對且日數相等;年度累計 ≤ 180;每段住院至多一項手術定額。

    v3 全量:9 案金額錯中 7 案為結構錯(漏第11條 ×4、兩項手術皆列、195 日未截 180、間隔 15 日仍合併)。
    """
    if result.decision is not ClaimDecision.APPROVE:
        return []
    code = _policy_code_of(bundle)
    if code is None:
        return []
    flags: list[GateFlag] = []

    def qty(clause: str) -> int:
        return sum(ln.quantity for ln in result.calculation
                   if normalize_clause_no(ln.clause_no) == clause and ln.kind is CalcKind.PER_UNIT)

    def has(clause: str) -> bool:
        return any(normalize_clause_no(ln.clause_no) == clause for ln in result.calculation)

    if code == "POL-A":
        daily = qty("第7條") + qty("第8條")
        if (has("第7條") or has("第8條")) != has("第11條"):
            flags.append(GateFlag(check="calc_structure_mismatch",
                                  detail="POL-A 第7/8條(日額)與第11條(輔助金)必須成對出現(第11條依第7條住院日數給付)"))
        elif has("第11條") and qty("第11條") != daily:
            flags.append(GateFlag(check="calc_structure_mismatch",
                                  detail=f"POL-A 第11條日數 {qty('第11條')} ≠ 第7條 + 第8條日數 {daily}"
                                         "(加護日數計入住院日數)"))
        cap = ANNUAL_CAP_DAYS["POL-A"]
        if daily > cap or qty("第11條") > cap:
            flags.append(GateFlag(check="calc_structure_mismatch",
                                  detail=f"POL-A 年度累計日數 {max(daily, qty('第11條'))} 超過第13條 {cap} 日上限"))
    if code == "POL-B":  # F2(SPEC-f):住院手術(第7條/附錄一全額行)必配第12條慰問金;僅門診手術(第8條)不得有慰問金
        inpatient = any(normalize_clause_no(ln.clause_no) in SURGERY_LUMP_CLAUSES["POL-B"]
                        and is_full_surgery_line(code, ln) for ln in result.calculation)
        if inpatient != has("第12條") and (inpatient or has("第8條")):
            flags.append(GateFlag(check="calc_structure_mismatch",
                                  detail="POL-B 住院手術(第7條)須配第12條住院慰問金;門診手術(第8條)不得有慰問金"
                                         f"(住院手術行={inpatient}, 第12條行={has('第12條')})"))
    surgery = SURGERY_LUMP_CLAUSES.get(code)
    if surgery:
        n_full = sum(1 for ln in result.calculation
                     if normalize_clause_no(ln.clause_no) in surgery and is_full_surgery_line(code, ln))
        limit = max(1, len(bundle.diagnosis_certificate.admissions))
        if n_full > limit:
            flags.append(GateFlag(check="calc_structure_mismatch",
                                  detail=f"{code} 手術定額行 {n_full} 筆超過住院段數 {limit}"
                                         "(同一次住院以最高一項為限)"))
    return flags


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def _check_waiting_period(result: AdjudicationResult, bundle: ClaimBundle) -> list[GateFlag]:
    """approve 且疾病類、事件日距生效日 < WAITING_PERIOD_DAYS → waiting_period_unmet;日期無法解析 → fail-closed。

    事件日 = 第一段住院之入院日;無住院(門診手術)取收據日。ICD S/T 章視為傷害,不檢。
    只擋不判:程式不下 deny(deny 屬模型判定與引用義務),改派人工。第23條 90 日特定疾病、
    第24條等待期內已診斷之延續、既往症不可算,交 prompt(A1)。
    """
    if result.decision is not ClaimDecision.APPROVE:
        return []
    icd = bundle.diagnosis_certificate.icd_code.value.strip().upper()
    if icd.startswith(INJURY_ICD_PREFIXES):
        return []
    adms = bundle.diagnosis_certificate.admissions
    event_raw = adms[0].admission_date.value if adms else bundle.medical_receipt.receipt_date.value
    effective = _parse_date(bundle.claim_application.policy_effective_date.value)
    event = _parse_date(event_raw)
    if effective is None or event is None:
        return [GateFlag(check="waiting_period_unverifiable",
                         detail=f"生效日 {bundle.claim_application.policy_effective_date.value!r} 或事件日 "
                                f"{event_raw!r} 非 ISO 日期,無法確認等待期")]
    elapsed = (event - effective).days
    if elapsed < WAITING_PERIOD_DAYS:
        return [GateFlag(check="waiting_period_unmet",
                         detail=f"生效日 {effective} 至事件日 {event} 僅 {elapsed} 日,"
                                f"未滿 {WAITING_PERIOD_DAYS} 日疾病等待期(ICD {icd or '未填'})")]
    return []


def routes_to_deny(flags: list[GateFlag], bundle: ClaimBundle) -> bool:
    """SPEC-p P2:deny 類旗標且生效日抽取可信(信心 ≥ 門檻、日期欄位無解析警示)→ 自動拒付;否則維持人工。

    pre-mortem:生效日被 DI 讀錯一年會算出假的「未滿等待期」,低信心時不得自動拒付。
    """
    if not any(f.check in DENY_FLAGS for f in flags):
        return False
    if bundle.claim_application.policy_effective_date.confidence < EXTRACTION_FIELD_CONFIDENCE_THRESHOLD:
        return False
    return not derive_facts(bundle).warnings


def routes_to_manual(flags: list[GateFlag]) -> bool:
    """任一「會改派」旗標即轉人工;NON_ROUTING_FLAGS 只記錄(條號誤植:引文有據)。"""
    return any(f.check not in NON_ROUTING_FLAGS for f in flags)


def run_gate(
    result: AdjudicationResult,
    bundle: ClaimBundle,
    index: dict[str, dict[str, Clause]] | None = None,
) -> GateResult:
    index = index if index is not None else load_policy_index()
    flags = [
        *_check_citations(result, index),
        *_check_amount(result, bundle),
        *_check_waiting_period(result, bundle),
        *_check_calculation_reference(result, bundle),
        *_check_calculation_days(result, bundle),
        *_check_calculation_structure(result, bundle),
        *_check_cap_sum(result, bundle),
        *_check_segment_cap(result, bundle),
        *_check_surgery_grades(result, bundle),
        *_check_surgery_grade_value(result, bundle),
        *_check_surgery_repeat(result, bundle),
        *_check_prior_diagnosis(result, bundle),
        *_check_stay_cap_merged(result, bundle),
        *_check_stay_days_coverage(result, bundle),
        *_check_decision_reasoning(result),
        *_check_reviews(result, bundle),
        *_check_receipt_coverage(result, bundle),
        *_check_receipt_amount_coverage(result, bundle),
        *_check_deduction_without_copy(result, bundle),
        *_check_deduction_bucket(result, bundle),
        *_check_deduction_unallocated(result, bundle),
        *_check_pre_existing_deny(result, bundle),
        *_check_surgery_definition_deny(result, bundle),
        *_check_ratio_consistency(result, bundle),
        *_check_claim_deadline(result, bundle),
        *_check_special_waiting_period(result, bundle),
        *_check_routing(result, bundle),
    ]
    final = result.decision if not routes_to_manual(flags) else ClaimDecision.MANUAL_REVIEW
    if routes_to_deny(flags, bundle):
        final = ClaimDecision.DENY
    return GateResult(
        claim_id=result.claim_id,
        original_decision=result.decision,
        final_decision=final,
        flags=flags,
        passed=not flags,
    )
