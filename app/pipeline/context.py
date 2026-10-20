"""條款上下文策略(SPEC 2026-10-01 行為 1、2、10;A/B 實驗的切換點)。

- full:整張保單全部條款(量測:每張 2,200~2,600 字,約 3.3k tokens)
- core+search:標題規則核心集(policy_index.core_clause_nos)∪ hybrid 檢索 top-k;
  真實量測 core ∪ top-10 對 71 案真值依據覆蓋 1.000(2026-10-01)
兩者皆只回傳同一張保單的條款、無重複、保持條款庫順序。
"""

from __future__ import annotations

import re
from typing import Protocol

from app.gate.policy_index import Clause, core_clause_nos

STRATEGIES = ("full", "core+search")
_POLICY_NO_RE = re.compile(r"^(POL-[A-Z])-")
_POLICY_NO_IN_TEXT_RE = re.compile(r"(POL-[A-Z])-\d{4}-\d{2}")


class Retriever(Protocol):
    def search(self, query: str, policy_code: str, k: int) -> list[Clause]: ...


def policy_code_from_number(policy_number: str) -> str | None:
    """保單號碼 POL-A-2026-01 → POL-A;無法辨識回傳 None(申請書缺件/保單未知)。"""
    m = _POLICY_NO_RE.match(policy_number.strip())
    return m.group(1) if m else None


def find_policy_code_in_text(lines: list[str]) -> str | None:
    """在版面文字行中搜尋保單號碼(容忍前後標點與引號),回傳保單代號。"""
    for ln in lines:
        m = _POLICY_NO_IN_TEXT_RE.search(ln)
        if m:
            return m.group(1)
    return None


def build_policy_context(
    policy_code: str,
    strategy: str,
    index: dict[str, dict[str, Clause]],
    retriever: Retriever | None = None,
    query: str = "",
    k: int = 10,
) -> list[Clause]:
    if strategy not in STRATEGIES:
        raise ValueError(f"未知條款上下文策略 {strategy!r},可用:{STRATEGIES}")
    clauses = index[policy_code]
    if strategy == "full":
        return list(clauses.values())
    if retriever is None:
        raise ValueError("core+search 需要檢索器")
    wanted = set(core_clause_nos(clauses))
    for hit in retriever.search(query, policy_code, k):
        if hit.policy_code == policy_code and hit.clause_no in clauses:  # 他保單命中一律濾掉
            wanted.add(hit.clause_no)
    return [c for cn, c in clauses.items() if cn in wanted]  # 依條款庫順序


def estimate_tokens(clauses: list[Clause]) -> int:
    """粗估 tokens(中文約 1.3 token/字),僅供成本比較與 dry-run 報表。"""
    chars = sum(len(c.title) + len(c.text) for c in clauses)
    return int(chars * 1.3)


def render_context(clauses: list[Clause]) -> str:
    """條款上下文的 prompt 文字:每條一段,標頭含保單代號與條號,供 quote 逐字引用。"""
    return "\n\n".join(f"【{c.policy_code} {c.clause_no}({c.title})】\n{c.text}" for c in clauses)
