"""條款庫索引:解析 data/policies/POL-*.md 為可反查的條款字典。

用途:Gate 的引用存在性檢核(citation 的 policy_id+clause_no 必須真實存在,
quote 必須與原文一致)。格式已全量盤點:條款標頭一律 `**第X條(標題)**`
(中文數字),附錄標頭一律 `## 附錄一/二`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

POLICIES_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CLAUSE_RE = re.compile(r"^\*\*第([一二三四五六七八九十]+)條(?:[((]([^))]*)[))])?\*\*")
_APPENDIX_RE = re.compile(r"^## (附錄[一二三四五六七八九十]+)")


def cn_to_int(cn: str) -> int:
    """中文數字轉整數(支援 1~99:七、十、十二、二十、三十六)。"""
    if cn == "十":
        return 10
    if "十" in cn:
        tens_part, _, units_part = cn.partition("十")
        tens = _CN_DIGITS[tens_part] if tens_part else 1
        units = _CN_DIGITS[units_part] if units_part else 0
        return tens * 10 + units
    return _CN_DIGITS[cn]


_INT_TO_CN = {v: k for k, v in _CN_DIGITS.items()}


def normalize_clause_no(raw: str) -> str:
    """把 LLM 各種寫法的條號收斂為索引鍵格式(第7條 / 附錄一)。

    接受:第七條、第 7 條、7、第7條第三款(款項後綴忽略)、附錄 1、附錄一。
    無法辨識者原樣回傳(由 Gate 判為不存在)。
    """
    s = re.sub(r"\s+", "", raw)
    m = re.match(r"^附錄([一二三四五六七八九十]+|\d+)", s)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else cn_to_int(m.group(1))
        return f"附錄{_INT_TO_CN.get(n, str(n))}"
    m = re.match(r"^第?([一二三四五六七八九十]+|\d+)(?:條|$)", s)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else cn_to_int(m.group(1))
        return f"第{n}條"
    return raw


_POLICY_NUMBER_RE = re.compile(r"^(POL-[A-Z])(?:-\d{4}-\d{2})?$")


def normalize_policy_id(raw: str) -> str:
    """保單代號正規化:保單號碼 POL-A-2026-01 → POL-A;已是代號則原樣;無法辨識原樣回傳(由 Gate 判為不存在)。

    2026-10-01 C008 真實觸發:模型以保單號碼當 policy_id,與條號正規化同精神補齊。
    """
    s = raw.strip()
    m = _POLICY_NUMBER_RE.match(s)
    return m.group(1) if m else s


@dataclass(frozen=True)
class Clause:
    policy_code: str  # 如 POL-A
    clause_no: str  # 如 第7條 或 附錄一
    title: str
    text: str  # 條文全文(不含標頭)


def parse_policy(path: Path) -> dict[str, Clause]:
    """解析單一保單檔,回傳 {clause_no: Clause}。"""
    policy_code = path.stem  # POL-A.md → POL-A(與 load_policy_index 的 key 規則一致)
    clauses: dict[str, Clause] = {}
    current_key: str | None = None
    current_title = ""
    buf: list[str] = []

    def flush() -> None:
        if current_key is not None:
            clauses[current_key] = Clause(
                policy_code=policy_code,
                clause_no=current_key,
                title=current_title,
                text="\n".join(buf).strip(),
            )

    for line in path.read_text(encoding="utf-8").splitlines():
        m = _CLAUSE_RE.match(line)
        a = _APPENDIX_RE.match(line)
        if m:
            flush()
            current_key = f"第{cn_to_int(m.group(1))}條"
            current_title = m.group(2) or ""
            buf = []
        elif a:
            flush()
            current_key = a.group(1)
            current_title = a.group(1)
            buf = []
        elif line.startswith("## ") or line.startswith("---"):
            flush()
            current_key = None
            buf = []
        elif current_key is not None:
            buf.append(line)
    flush()
    return clauses


_ENUM_LINE_RE = re.compile(r"^([一二三四五六七八九十]+)、")
_NUMBERED_LINE_RE = re.compile(r"^(\d+)\.")


def clause_segments(clause: Clause) -> dict[str, str]:
    """SPEC-aj 段落位置 → 原文行(條文子字串;程式依模型所指 segment 填 quote)。

    104 條全量盤點只有 5 種形狀:單段 → 本文;本文+列舉 → 本文、一…N;其後再有段落 → 末段;
    附錄表格 → 等級欄值(略去表頭與分隔列);附錄二編號清單 → 1…N。
    """
    lines = [ln.strip() for ln in clause.text.split("\n") if ln.strip()]
    segs: dict[str, str] = {}
    if lines and lines[0].startswith("|"):
        for ln in lines[1:]:
            if not ln.startswith("|---"):
                segs[ln.strip("|").split("|")[0].strip()] = ln
        return segs
    seen_enum = False
    for ln in lines:
        m = _ENUM_LINE_RE.match(ln) or _NUMBERED_LINE_RE.match(ln)
        if m:
            seen_enum = True
            segs[m.group(1)] = ln
        else:
            segs["末段" if seen_enum else "本文"] = ln
    return segs


_SEG_ITEM_RE = re.compile(r"^第?([一二三四五六七八九十]+)[項款]")
_SEG_GRADE_RE = re.compile(r"^第?([一二三四五六七八九十]+|\d)級")


def normalize_segment(raw: str) -> str:
    """模型 segment 寫法收斂(一、/第一項/第一款 → 一;第二級手術/二級 → 第二級;4. → 4)。

    無法辨識者原樣回傳(由 Gate 判不存在)。
    """
    s = re.sub(r"\s+", "", raw).rstrip("、.")
    m = _SEG_GRADE_RE.match(s)
    if m:
        n = m.group(1)
        return f"第{_INT_TO_CN.get(int(n), n) if n.isdigit() else n}級"  # 線上 C054:「第1級」
    m = _SEG_ITEM_RE.match(s)
    return m.group(1) if m else s


def load_policy_index(policies_dir: Path = POLICIES_DIR) -> dict[str, dict[str, Clause]]:
    """回傳 {policy_code: {clause_no: Clause}},如 index['POL-A']['第7條']。"""
    return {p.stem: parse_policy(p) for p in sorted(policies_dir.glob("POL-*.md"))}


# 結構性核心條款:只依「條款標題類型」判定,與案例真值無關(避免 eval 洩題)。
# 判準(2026-10-01 人工核定,xlsx 104 條逐列):「不論案情為何都要翻開」的條款——
# 名詞定義、給付計算、認定/上限、等待期與時效(需日期相減才知是否觸發)、程序出口、附錄表。
# 刻意排除:除外責任、契約效力、通知義務等情境條款(案情相關時由 hybrid 檢索依文件內容命中)。
# 用途:Policy Agent 條款上下文策略 core+search。量測:純 top-5 0.655、純 top-10 0.835、
# 核心集(初版 43 條)∪ top-10 真實 0.986(70/71,僅 C067 漏時效兩條)→ 核定後加時效等 7 條。
# 規則順序即類別優先序:「程序」在「給付」前,讓「保險金之給付期限」歸程序(人工核定備註)。
CORE_TITLE_RULES: tuple[tuple[str, str], ...] = (
    ("定義", r"名詞定義"),
    ("程序", r"應備文件|補正|給付期限|爭議之處理|收據正本"),
    ("給付", r"保險金$|保險金之|給付$|給付總則|未列表手術"),
    ("認定/上限", r"同一次住院|日數上限|給付日數|再手術"),
    ("等待期/時效", r"等待期|時效"),
    ("附錄", r"^附錄"),
)


def core_clause_nos(clauses: dict[str, Clause]) -> list[str]:
    """回傳一張保單的核心條款鍵(依 CORE_TITLE_RULES,保持原順序)。"""
    out = []
    for cn, c in clauses.items():
        if any(re.search(pat, c.title or cn) for _, pat in CORE_TITLE_RULES):
            out.append(cn)
    return out

