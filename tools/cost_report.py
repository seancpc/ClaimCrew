"""SPEC 2026-10-02-k K3:逐呼叫 token 彙總與價目表計價(ADR-007 分層路由實測的成本量測)。

用法:
    python3 tools/cost_report.py check/pred_full_v9/_run.jsonl [check/pred_route_v1/_run.jsonl ...]
每個檔案一段:各呼叫類型(extraction / decision;Phase 1 舊格式無 calls 鍵 → all)的
prompt / cached / completion token 合計與佔比、每案平均、金額(模型在 PRICES 內才算,否則 n/a)。
金額與用量分欄、價目表日期與來源印在表頭(pre-mortem:調價/換模型後數字可稽核)。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

# 價目表:USD / 1M tokens;鍵為回應 model 欄位(如 gpt-4o-2024-11-20)。初始為空——由使用者自 Azure 定價頁確認後填入。
PRICES: dict[str, dict[str, Decimal]] = {
    # Azure Retail Prices API 2026-10-02 查詢,serviceName 'Foundry Models'、armRegionName eastus2、
    # meter「… regnl Tokens」(regional = Standard SKU);API 單位 1K → ×1000 換算為 1M。
    "gpt-4o-2024-11-20": {"input": Decimal("2.75"), "cached_input": Decimal("1.375"), "output": Decimal("11.00")},
    "gpt-4.1-mini-2025-04-14": {"input": Decimal("0.44"), "cached_input": Decimal("0.11"), "output": Decimal("1.76")},
}
PRICES_AS_OF = "2026-10-02(meter 生效日:gpt-4o 1120 2024-12-01、gpt-4.1 mini 2025-11-01)"
PRICES_SOURCE = "https://prices.azure.com/api/retail/prices serviceName='Foundry Models' armRegionName='eastus2' regnl"
_M = Decimal(1_000_000)
_Q = Decimal("0.0001")


def call_cost_usd(*, prompt: int, cached: int, completion: int, prices: dict[str, Decimal]) -> Decimal:
    """(prompt − cached) × input + cached × cached_input + completion × output,四捨五入至 0.0001 USD。"""
    raw = ((Decimal(prompt - cached) * prices["input"] + Decimal(cached) * prices["cached_input"]
            + Decimal(completion) * prices["output"]) / _M)
    return raw.quantize(_Q, rounding=ROUND_HALF_UP)


@dataclass
class CallAgg:
    n: int = 0
    prompt: int = 0
    cached: int = 0
    completion: int = 0
    usd: Decimal | None = Decimal(0)  # 任一筆模型不在價目表 → None(n/a)
    models: set[str] = field(default_factory=set)

    @property
    def cached_share(self) -> float:
        return self.cached / self.prompt if self.prompt else 0.0

    def add(self, call: dict[str, Any]) -> None:
        self.n += 1
        p = int(call.get("prompt_tokens", 0))
        c, o = int(call.get("cached_tokens", 0)), int(call.get("completion_tokens", 0))
        self.prompt += p
        self.cached += c
        self.completion += o
        model = str(call.get("model", ""))
        self.models.add(model)
        price = PRICES.get(model)
        if price is None:
            self.usd = None
        elif self.usd is not None:
            self.usd += call_cost_usd(prompt=p, cached=c, completion=o, prices=price)


@dataclass
class Summary:
    path: Path
    n_cases: int = 0
    n_ok: int = 0
    by_call: OrderedDict[str, CallAgg] = field(default_factory=OrderedDict)

    @property
    def total_prompt(self) -> int:
        return sum(a.prompt for a in self.by_call.values())

    def prompt_share(self, name: str) -> float:
        return self.by_call[name].prompt / self.total_prompt if self.total_prompt else 0.0

    @property
    def total_usd(self) -> Decimal | None:
        if any(a.usd is None for a in self.by_call.values()):
            return None
        return sum((a.usd for a in self.by_call.values() if a.usd is not None), Decimal(0))


def _calls_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    if "calls" in row:
        return list(row["calls"])
    # Phase 1 舊格式:只有合計欄位,無 cached;歸為 all
    return [{"name": "all", "model": row.get("model", ""), "prompt_tokens": row.get("prompt_tokens", 0),
             "cached_tokens": 0, "completion_tokens": row.get("completion_tokens", 0)}]


def summarize(path: Path) -> Summary:
    s = Summary(path=path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        s.n_cases += 1
        s.n_ok += row.get("status") == "ok"
        for call in _calls_of(row):
            s.by_call.setdefault(str(call.get("name", "all")), CallAgg()).add(call)
    return s


def _fmt_usd(v: Decimal | None) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def render(s: Summary) -> str:
    lines = [f"## {s.path}", f"案數 {s.n_cases}(ok {s.n_ok});總 prompt {s.total_prompt}",
             "| 呼叫 | 次數 | prompt | cached(佔 prompt) | completion | prompt 佔比 | USD |",
             "|---|---|---|---|---|---|---|"]
    for name, a in s.by_call.items():
        lines.append(f"| {name} | {a.n} | {a.prompt} | {a.cached}({a.cached_share:.1%}) | {a.completion} "
                     f"| {s.prompt_share(name):.1%} | {_fmt_usd(a.usd)} |")
    per_case = None if s.total_usd is None or not s.n_cases else (s.total_usd / s.n_cases).quantize(_Q)
    lines.append(f"合計 USD {_fmt_usd(s.total_usd)};每案 USD {_fmt_usd(per_case)};模型 "
                 + ", ".join(sorted({m for a in s.by_call.values() for m in a.models if m})))  # 缺件案無呼叫,model 為空
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_logs", nargs="+", help="一個或多個 _run.jsonl(多個並排比較)")
    args = ap.parse_args(argv)
    print(f"# cost_report — 價目表日期:{PRICES_AS_OF};來源:{PRICES_SOURCE};用量與金額分欄,模型不在表內則 USD=n/a")
    for p in args.run_logs:
        print()
        print(render(summarize(Path(p))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
