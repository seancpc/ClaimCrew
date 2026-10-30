"""SPEC-ab:列舉條款引文整項照抄(C035 第9條 15 輪 5 次把第一項開頭「附錄一「手術等級表」」拼到第三項;
C070 第10條把分項改以「、」串接)。規則 2 增補;並驗證「依規則引用」的形式能通過 Gate 反查、拼接形式不能。"""
from __future__ import annotations

from pathlib import Path

from app.gate.checks import quote_matches
from app.gate.policy_index import load_policy_index

ROOT = Path(__file__).resolve().parents[1]
POLC = load_policy_index()["POL-C"]
PRE9 = "被保險人於住院期間接受手術者,本公司按其實際發生之手術費及麻醉費,於下列限額內給付「手術費用保險金」:"


def _rule_2() -> str:
    text = (ROOT / "app" / "agents" / "prompts" / "adjudication.md").read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("2. **理由逐條綁引用**"))


def test_ab1_rule_2_enumeration_addendum_withdrawn():
    """v14 撤回(EVIDENCE 第三十六部分):放行錯 C029、前 72 案 Gate 後 −6,依事前判準撤回規則 2 增補。"""
    r = _rule_2()
    assert "整項照抄" not in r and "以「…」隔開" not in r and "不得把不同分項的文字拼成一句" not in r


def test_ab1_rule_2_keeps_existing_prohibitions():
    r = _rule_2()
    assert "**禁止**引用清單之外的條款" in r and "禁止自創條款編號或改寫引文" in r


def test_ab2_compliant_skip_form_passes_gate():
    q = PRE9 + "…三、第三級手術:限額為新臺幣捌萬元之百分之一百。"
    assert quote_matches(q, POLC["第9條"].text)


def test_ab2_spliced_form_still_fails_gate():
    q = PRE9 + "附錄一「手術等級表」第三級手術:限額為新臺幣捌萬元之百分之一百。"
    assert not quote_matches(q, POLC["第9條"].text)


def test_ab2_comma_joined_with_trailing_paraphrase_fails_gate():
    """cloud1 C070 形狀:分項以「、」串接後再加原文沒有的「等相關項目」。(相鄰兩項純「、」串接 Gate 會依 R1 接受。)"""
    q = ("本公司按被保險人住院期間實際發生之下列費用,於每次住院新臺幣陸萬元之限額內給付「住院雜費保險金」:"
         "醫療器材及衛材費用、檢查、檢驗及放射線診療費用等相關項目。")
    assert not quote_matches(q, POLC["第10條"].text)


def test_ab2_compliant_whole_items_pass_gate():
    q = ("本公司按被保險人住院期間實際發生之下列費用,於每次住院新臺幣陸萬元之限額內給付「住院雜費保險金」:"
         "一、醫師指示用藥及處方藥。二、醫療器材及衛材費用。")
    assert quote_matches(q, POLC["第10條"].text)
