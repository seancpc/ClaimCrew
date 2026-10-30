"""structured outputs 轉換層的不變量(property-based;證據優先流程 gauntlet)。

契約方向:LLM 依轉換後 schema 產出 JSON(金額為字串)→ Pydantic 解析。
故不變量 c 驗的是「同一份 LLM 形狀的 payload,轉換後 schema 與 Pydantic
兩邊都接受」——兩個驗證器對契約的認定必須一致。
"""

import json

import jsonschema
from hypothesis import given, settings
from hypothesis import strategies as st

from app.schemas.claim import (
    AdjudicationDraft,
    AdjudicationResult,
    ClaimApplication,
    ClaimBundle,
    ClaimDecision,
    DiagnosisCertificate,
    ExtractedField,
    IncidentStatement,
    IntakeResult,
    MedicalReceipt,
    PolicyCitation,
    PolicyResult,
    ReceiptItem,
    _enforce_subset,
    to_structured_output_schema,
)

ALL_MODELS = [
    AdjudicationDraft,
    ExtractedField,
    DiagnosisCertificate,
    ReceiptItem,
    MedicalReceipt,
    ClaimApplication,
    ClaimBundle,
    IncidentStatement,
    PolicyCitation,
    IntakeResult,
    PolicyResult,
    AdjudicationResult,
]


def _object_nodes(schema: dict):
    stack = [schema, *schema.get("$defs", {}).values()]
    while stack:
        node = stack.pop()
        if node.get("type") == "object" and "properties" in node:
            yield node
            stack.extend(node["properties"].values())
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            stack.append(node["items"])


def test_property_every_declared_model_closed_and_required():
    """不變量 a:所有模型、所有 object 節點皆封閉且全欄位 required。"""
    for model in ALL_MODELS:
        for node in _object_nodes(to_structured_output_schema(model)):
            assert node["additionalProperties"] is False, model.__name__
            assert node["required"] == list(node["properties"].keys()), model.__name__


def test_property_transform_idempotent():
    """不變量 b:轉換冪等——對已轉換 schema 再套用子集強制,結果不變。"""
    for model in ALL_MODELS:
        once = to_structured_output_schema(model)
        twice = json.loads(json.dumps(once))
        _enforce_subset(twice)
        for def_schema in twice.get("$defs", {}).values():
            _enforce_subset(def_schema)
        assert twice == once, model.__name__


@given(
    claim_id=st.text(min_size=1, max_size=10),
    decision=st.sampled_from([d.value for d in ClaimDecision]),
    amount=st.decimals(min_value=0, max_value=10**9, places=0),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    citations=st.lists(
        st.fixed_dictionaries(
            {
                "policy_id": st.text(min_size=1, max_size=10),
                "clause_no": st.text(min_size=1, max_size=10),
                "quote": st.text(max_size=80),
                "relevance": st.text(max_size=80),
                "segment": st.text(max_size=10),
            }
        ),
        max_size=3,
    ),
)
@settings(max_examples=50, deadline=None)
def test_property_llm_shaped_payload_accepted_by_both_validators(
    claim_id, decision, amount, confidence, citations
):
    """不變量 c:LLM 形狀 payload(金額字串)同時通過 schema 驗證與 Pydantic 解析。

    B1(2026-10-01-b)後 LLM 形狀為 AdjudicationDraft:金額改以 calculation 明細字串傳遞,無 approved_amount。
    """
    payload = {
        "claim_id": claim_id,
        "decision": decision,
        "calculation": [{"item": "<項目>", "clause_no": "第7條", "kind": "per_unit", "quantity": 1,
                         "unit_amount": str(amount), "actual_amount": "0", "deduction": "0", "cap": "0",
                         "lump_amount": "0", "ratio": "1"}],
        "citations": citations,
        "reasoning": "",
        "confidence": confidence,
        "undisputed_calculation": [],  # SPEC-x:strict schema 全欄位必填
        "disputed_items": [],
    }
    schema = to_structured_output_schema(AdjudicationDraft)
    jsonschema.validate(payload, schema)
    parsed = AdjudicationDraft.model_validate(payload)
    assert parsed.decision == ClaimDecision(decision)
    assert str(parsed.calculation[0].unit_amount) == str(amount)


def test_enforce_subset_reaches_inline_array_of_object():
    """直接驗函式核心語意:inline(非 $ref)的 array-of-object 也必須被強制。

    現有模型的 array items 皆為 $ref(由 $defs 迴圈接住),此測試守住
    未來出現 inline 巢狀形狀時的行為(mutation M3 揭露的盲點)。
    """
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            }
        },
    }
    _enforce_subset(schema)
    inner = schema["properties"]["rows"]["items"]
    assert inner["additionalProperties"] is False
    assert inner["required"] == ["name"]
