"""SPEC 2026-10-02-k K3:tools/cost_report.py——逐呼叫 token 彙總與價目表計價。"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tools import cost_report as cr


def _write(tmp: Path, name: str, rows: list[dict]) -> Path:
    p = tmp / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


NEW_ROWS = [
    {"claim_id": "C001", "status": "ok", "model": "gpt-4o-2024-11-20", "prompt_tokens": 1500, "completion_tokens": 150,
     "calls": [{"name": "extraction", "model": "gpt-4o-2024-11-20", "prompt_tokens": 1000, "cached_tokens": 600,
                "completion_tokens": 100, "latency_s": 1.0},
               {"name": "decision", "model": "gpt-4o-2024-11-20", "prompt_tokens": 500, "cached_tokens": 0,
                "completion_tokens": 50, "latency_s": 1.0}]},
    {"claim_id": "C002", "status": "error", "model": "gpt-4o-2024-11-20", "prompt_tokens": 1000,
     "completion_tokens": 100,
     "calls": [{"name": "extraction", "model": "gpt-4o-2024-11-20", "prompt_tokens": 1000, "cached_tokens": 0,
                "completion_tokens": 100, "latency_s": 1.0}]},
]
OLD_ROWS = [  # Phase 1 八輪的格式:無 calls 鍵
    {"claim_id": "C001", "status": "ok", "model": "gpt-4o-2024-11-20", "prompt_tokens": 1500, "completion_tokens": 150},
]


def test_k3_cost_formula_hand_computed():
    prices = {"input": Decimal("2.50"), "cached_input": Decimal("1.25"), "output": Decimal("10.00")}
    # (1000 − 600) × 2.50 + 600 × 1.25 + 100 × 10.00 = 1000 + 750 + 1000 = 2750 → / 1e6
    assert cr.call_cost_usd(prompt=1000, cached=600, completion=100, prices=prices) == Decimal("0.0028")
    assert cr.call_cost_usd(prompt=1000, cached=0, completion=0, prices=prices) == Decimal("0.0025")


def test_k3_summary_groups_by_call_name_with_cached_share(tmp_path: Path):
    s = cr.summarize(_write(tmp_path, "_run.jsonl", NEW_ROWS))
    assert s.n_cases == 2 and s.n_ok == 1
    ext, dec = s.by_call["extraction"], s.by_call["decision"]
    assert (ext.prompt, ext.cached, ext.completion, ext.n) == (2000, 600, 200, 2)
    assert (dec.prompt, dec.cached, dec.completion, dec.n) == (500, 0, 50, 1)
    assert ext.cached_share == pytest.approx(0.3)
    assert s.total_prompt == 2500 and s.prompt_share("extraction") == pytest.approx(0.8)


def test_k3_old_format_falls_back_to_all(tmp_path: Path):
    s = cr.summarize(_write(tmp_path, "_run.jsonl", OLD_ROWS))
    assert list(s.by_call) == ["all"] and s.by_call["all"].prompt == 1500 and s.by_call["all"].cached == 0


def test_k3_missing_price_prints_na_and_exits_zero(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(cr, "PRICES", {})
    p = _write(tmp_path, "_run.jsonl", NEW_ROWS)
    assert cr.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "n/a" in out and "2000" in out and cr.PRICES_AS_OF in out


def test_k3_with_price_table_reports_usd_total(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(cr, "PRICES", {"gpt-4o-2024-11-20": {"input": Decimal("2.50"), "cached_input": Decimal("1.25"),
                                                             "output": Decimal("10.00")}})
    p = _write(tmp_path, "_run.jsonl", NEW_ROWS)
    assert cr.main([str(p)]) == 0
    out = capsys.readouterr().out
    # C001: 0.0028 + (500×2.50 + 50×10)/1e6 = 0.0028 + 0.00175 = 0.00455;C002: 1000×2.5+100×10 = 0.0035 → 總 0.00805
    assert "0.0081" in out or "0.00805" in out


def test_k3_multiple_files_side_by_side(tmp_path: Path, capsys):
    a = _write(tmp_path, "a.jsonl", NEW_ROWS)
    b = _write(tmp_path, "b.jsonl", OLD_ROWS)
    assert cr.main([str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "a.jsonl" in out and "b.jsonl" in out


def test_k3_price_table_pins_both_deployed_models_in_usd_per_million():
    # 2026-10-02 Retail Prices API(eastus2 regnl,1K 單價 ×1000):鍵為回應 model 字串
    assert cr.PRICES["gpt-4o-2024-11-20"] == {"input": Decimal("2.75"), "cached_input": Decimal("1.375"),
                                              "output": Decimal("11.00")}
    assert cr.PRICES["gpt-4.1-mini-2025-04-14"] == {"input": Decimal("0.44"), "cached_input": Decimal("0.11"),
                                                    "output": Decimal("1.76")}
    assert "2026-10-02" in cr.PRICES_AS_OF and "prices.azure.com" in cr.PRICES_SOURCE
