"""claim schema 與 structured outputs 轉換層的驗收斷言(方案 B 假設 1 驗證)。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.claim import (
    AdjudicationDraft,
    AdjudicationResult,
    ClaimBundle,
    ClaimDecision,
    DecisionResult,
    ExtractedField,
    IntakeResult,
    PolicyResult,
    is_nullable_anyof,
    to_structured_output_schema,
)


def _walk_objects(node: dict, defs: dict):
    """走訪 schema 中所有 object 節點(含 $defs 與巢狀)。"""
    if node.get("type") == "object" and "properties" in node:
        yield node
        for prop in node["properties"].values():
            yield from _walk_objects(prop, defs)
    if node.get("type") == "array" and isinstance(node.get("items"), dict):
        yield from _walk_objects(node["items"], defs)


# 全部走 structured outputs 的模型(含 B1 新增的 AdjudicationDraft / DecisionResult)
LLM_FACING_MODELS = [ClaimBundle, AdjudicationResult, AdjudicationDraft, DecisionResult, IntakeResult, PolicyResult]


def _all_object_nodes(model):
    schema = to_structured_output_schema(model)
    nodes = list(_walk_objects(schema, schema.get("$defs", {})))
    for def_schema in schema.get("$defs", {}).values():
        nodes.extend(_walk_objects(def_schema, {}))
    return nodes


@pytest.mark.parametrize("model", LLM_FACING_MODELS)
def test_every_object_forbids_additional_properties(model):
    nodes = _all_object_nodes(model)
    assert nodes, "schema 應至少含一個 object 節點"
    for node in nodes:
        assert node.get("additionalProperties") is False


@pytest.mark.parametrize("model", LLM_FACING_MODELS)
def test_every_field_is_required(model):
    for node in _all_object_nodes(model):
        assert node.get("required") == list(node["properties"].keys())


@pytest.mark.parametrize("model", LLM_FACING_MODELS)
def test_no_top_level_anyof_in_properties(model):
    """structured outputs 不接受屬性層 anyOf 混型(Decimal 需收斂為 string);唯一例外為 optional 之 [X, null]。"""
    for node in _all_object_nodes(model):
        for name, prop in node["properties"].items():
            assert "anyOf" not in prop or is_nullable_anyof(prop), f"{name} 仍含非 nullable 之 anyOf"


def test_optional_incident_statement_is_nullable_anyof():
    """ClaimBundle.incident_statement 為 optional:schema 為 [$ref, null] 且仍列入 required(structured outputs 慣例)。"""
    schema = to_structured_output_schema(ClaimBundle)
    prop = schema["properties"]["incident_statement"]
    assert is_nullable_anyof(prop)
    assert "incident_statement" in schema["required"]
    assert "IncidentStatement" in schema["$defs"]
    assert schema["$defs"]["IncidentStatement"]["additionalProperties"] is False


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        ExtractedField(value="x", confidence=0.5, unexpected="y")


def test_confidence_range_enforced():
    with pytest.raises(ValidationError):
        ExtractedField(value="x", confidence=1.5)


def test_adjudication_roundtrip():
    result = AdjudicationResult(
        claim_id="C001",
        decision=ClaimDecision.MANUAL_REVIEW,
        approved_amount=Decimal("0"),
        citations=[],
        reasoning="",
        confidence=0.2,
    )
    restored = AdjudicationResult.model_validate_json(result.model_dump_json())
    assert restored == result


def test_structured_output_schema_has_no_ref_siblings_or_numeric_bounds():
    """Azure OpenAI strict 模式實測(2026-10-01 aoai_smoke):`$ref` 不得帶兄弟關鍵字(description),
    數值邊界(minimum/maximum)亦非支援子集;邊界交由 Pydantic 驗證層把關。"""
    from app.schemas.claim import ClaimBundle, DecisionResult, to_structured_output_schema

    def walk(node, path=""):
        if isinstance(node, dict):
            if "$ref" in node:
                assert set(node) == {"$ref"}, f"{path}: $ref 帶兄弟 {sorted(node)}"
            for bad in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "default", "format"):
                assert bad not in node, f"{path}: 含不支援關鍵字 {bad}"
            for k, v in node.items():
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    for model in (ClaimBundle, DecisionResult):
        walk(to_structured_output_schema(model))


def test_money_fields_accept_thousands_separators_and_currency_marks():
    """2026-10-01 C008 第二輪:模型把收據「142,500」連逗號照抄,Decimal 解析失敗。
    金額欄位前置正規化(去逗號/幣別/全形),schema 型別仍為 string。"""
    from decimal import Decimal

    from app.schemas.claim import ReceiptItem, to_structured_output_schema

    assert ReceiptItem(item_name="x", amount="142,500").amount == Decimal("142500")
    assert ReceiptItem(item_name="x", amount="NT$ 1,000 元").amount == Decimal("1000")
    assert ReceiptItem(item_name="x", amount="１２３").amount == Decimal("123")
    assert ReceiptItem(item_name="x", amount=Decimal("5")).amount == Decimal("5")
    schema = to_structured_output_schema(ReceiptItem)
    assert schema["properties"]["amount"]["type"] == "string"


# --- 2026-10-02 v4 C064:模型把金額欄填成「ゼロ」「-」→ Decimal 解析失敗整案 error;明示為零的寫法由 schema 層吸收 ---
@pytest.mark.parametrize("raw", ["-", "—", "－", "零", "無", "ゼロ", "0元", "NT$0", "", " "])
def test_money_explicit_zero_spellings_parse_to_zero(raw):
    from app.schemas.claim import ReceiptItem
    assert ReceiptItem(item_name="x", amount=raw).amount == 0


@pytest.mark.parametrize("raw", ["abc", "一萬", "1,2,3,4x", "N/A", "null"])
def test_money_other_non_numeric_still_rejected(raw):
    from pydantic import ValidationError

    from app.schemas.claim import ReceiptItem
    with pytest.raises(ValidationError):
        ReceiptItem(item_name="x", amount=raw)
