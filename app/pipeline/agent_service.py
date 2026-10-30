"""Foundry Agent Service(Agents v2)共用層:agent 定義、Responses 引用、工具迴圈、usage。

SPEC-n 探針抽出,SPEC-o Repair 共用。

- 建立:`project.agents.create_version(name, definition=PromptAgentDefinition(...))`
- 呼叫:`project.get_openai_client().responses.create(input=..., extra_body={"agent_reference": {...}})`
  (2026-10-02 實證:`agent` 鍵回 400 deprecated)
- function tool 由客端執行(Gate),以 `function_call_output` + `previous_response_id` 回填,最多 MAX_ROUNDS 輪
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from azure.ai.projects.models import (
    FunctionTool,
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
)
from pydantic import ValidationError

from app.gate.checks import run_gate
from app.gate.policy_index import Clause
from app.pipeline.runner import finalize_adjudication
from app.schemas.claim import AdjudicationDraft, ClaimBundle, DecisionResult, to_structured_output_schema

GATE_TOOL_NAME = "run_gate"
AGENT_NAME_PREFIX = "claimcrew-probe"
MAX_ROUNDS = 2
# 高風險假設 1 已驗證(2026-10-02):用 "agent" 服務回 400 "The 'agent' property is deprecated. Use 'agent_reference'"
AGENT_REF_STYLES = ("agent_reference", "agent")

GATE_TOOL_INSTRUCTIONS = (
    f"\n\n---\n\n# 工具:{GATE_TOOL_NAME}\n"
    f"你有一個工具 `{GATE_TOOL_NAME}`,輸入為 AdjudicationDraft(與最終輸出的 adjudication 同結構),"
    "回傳程式側 Gate 的檢核旗標。"
    "**輸出最終結果前必須先以草稿呼叫此工具一次**;依回傳旗標修正草稿(引文須為條款原文連續片段、算式明細須對應條款金額與日數候選)。"
    "若旗標屬提醒型(如 unlisted_surgery_review、special_waiting_period_review、low_confidence*、receipt_*、"
    "pre_existing_review),"
    "無法以修改輸出消除,照實輸出即可。工具最多呼叫兩次,之後直接輸出最終結果。"
)


class AgentServiceError(ValueError):
    """Agent Service 層錯誤(回應解析/schema 驗證/未掛工具),訊息含 claim_id。"""


# ---------- agent 定義 ----------
def build_agent_definition(deployment: str, instructions: str, tool: str | None = None) -> PromptAgentDefinition:
    """strict json_schema 輸出 = DecisionResult;tool='gate' 時掛單一 strict function tool(參數 = AdjudicationDraft)。"""
    tools: list[Any] = []
    if tool == "gate":
        tools.append(FunctionTool(name=GATE_TOOL_NAME, strict=True,
                                  description="以判定草稿執行程式側 Gate 檢核,回傳 {passed, flags:[{check, detail}]}",
                                  parameters=to_structured_output_schema(AdjudicationDraft)))
        instructions = instructions + GATE_TOOL_INSTRUCTIONS
    fmt = TextResponseFormatJsonSchema(name="DecisionResult", strict=True,
                                       schema=to_structured_output_schema(DecisionResult))
    return PromptAgentDefinition(model=deployment, instructions=instructions, tools=tools,
                                 text=PromptAgentDefinitionTextOptions(format=fmt))


def agent_ref_body(name: str, version: str | None, style: str = "agent_reference") -> dict[str, Any]:
    ref: dict[str, Any] = {"name": name, "type": "agent_reference"}
    if version:
        ref["version"] = version
    return {style: ref}


# ---------- 回應解析 ----------
@dataclass
class Parsed:
    kind: str  # final | tool_call
    result: DecisionResult | None = None
    call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # 平行工具呼叫:全部 (call_id, 引數)


def parse_response(resp: Any, claim_id: str) -> Parsed:
    """Responses 物件 → final(DecisionResult)或 tool_call(call_id + 引數 dict);其他一律 AgentServiceError。"""
    calls: list[tuple[str, dict[str, Any]]] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) == "function_call":
            try:
                args = json.loads(item.arguments)
            except json.JSONDecodeError as e:
                raise AgentServiceError(f"{claim_id}:function_call 引數非 JSON:{e}") from e
            calls.append((item.call_id, args))
    if calls:  # 平行工具呼叫一律全部回填(2026-10-03:只回第一個 → 400 No tool output found for function call)
        return Parsed(kind="tool_call", call_id=calls[0][0], arguments=calls[0][1], calls=calls)
    text = (getattr(resp, "output_text", "") or "").strip()
    try:
        # 平行工具回填後 output_text 可能把多個 message 串成「兩個 JSON 相接」(R2 實證 trailing characters)
        # → 只取第一個完整 JSON 物件
        first, _end = json.JSONDecoder().raw_decode(text) if text.startswith("{") else (None, 0)
        payload = json.dumps(first, ensure_ascii=False) if first is not None else text
        return Parsed(kind="final", result=DecisionResult.model_validate_json(payload))
    except (ValidationError, json.JSONDecodeError) as e:
        head = str(e.errors()[:1]) if isinstance(e, ValidationError) else str(e)
        detail = f"{head[:200]};原文片段:{text[:120]}"
        raise AgentServiceError(f"{claim_id}:DecisionResult 驗證失敗:{detail}") from e


def usage_record(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    details = getattr(u, "input_tokens_details", None)
    return {"input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cached_tokens": int(getattr(details, "cached_tokens", 0) or 0)}


# ---------- Gate 工具 ----------
GateFn = Callable[[dict[str, Any]], dict[str, Any]]


def make_gate_fn(bundle: ClaimBundle, index: dict[str, dict[str, Clause]]) -> GateFn:
    """草稿 dict → finalize_adjudication → run_gate(與主路徑同源)→ {passed, flags}。"""
    def gate_fn(args: dict[str, Any]) -> dict[str, Any]:
        draft = AdjudicationDraft.model_validate(args)
        g = run_gate(finalize_adjudication(draft, bundle), bundle, index)
        return {"passed": g.passed, "flags": [{"check": f.check, "detail": f.detail} for f in g.flags]}
    return gate_fn


# ---------- 工具迴圈 ----------
@dataclass
class LoopResult:
    kind: str  # final | exhausted
    result: DecisionResult | None
    rounds: int
    flags_before: list[dict[str, Any]] | None
    usage: list[dict[str, int]]
    response_ids: list[str]


def tool_loop(client: Any, agent_ref: dict[str, Any], user_input: str, gate_fn: GateFn | None, claim_id: str,
              *, max_rounds: int = MAX_ROUNDS, style: str = "agent_reference") -> LoopResult:
    """初始呼叫 → 若 function_call 則執行 gate_fn 並以 previous_response_id 回填,最多 max_rounds 輪;
    超過回 exhausted。"""
    body = agent_ref_body(agent_ref["name"], agent_ref.get("version"), style)
    resp = client.responses.create(input=user_input, extra_body=body)
    usage, ids = [usage_record(resp)], [resp.id]
    parsed = parse_response(resp, claim_id)
    flags_before: list[dict[str, Any]] | None = None
    rounds = 0
    while parsed.kind == "tool_call":
        if rounds >= max_rounds:
            return LoopResult("exhausted", None, rounds, flags_before, usage, ids)
        if gate_fn is None:
            raise AgentServiceError(f"{claim_id}:agent 呼叫 {GATE_TOOL_NAME} 但本模式未掛工具")
        outputs = []
        for call_id, args in parsed.calls:
            out = gate_fn(args)
            if flags_before is None:
                flags_before = list(out["flags"])
            outputs.append({"type": "function_call_output", "call_id": call_id,
                            "output": json.dumps(out, ensure_ascii=False)})
        rounds += 1
        resp = client.responses.create(input=outputs, previous_response_id=resp.id, extra_body=body)
        usage.append(usage_record(resp))
        ids.append(resp.id)
        parsed = parse_response(resp, claim_id)
    return LoopResult("final", parsed.result, rounds, flags_before, usage, ids)


class AgentPool:
    """每保單一個 agent 版本(instructions 含該保單條款);結束時刪除(除非 keep)。mode: parity | gate | repair。"""

    def __init__(self, project: Any, deployment: str, mode: str, run_tag: str, prefix: str = AGENT_NAME_PREFIX):
        self.project, self.deployment, self.mode, self.run_tag, self.prefix = project, deployment, mode, run_tag, prefix
        self.created: dict[str, dict[str, Any]] = {}

    def get(self, code: str, system: str) -> dict[str, Any]:
        if code not in self.created:
            name = f"{self.prefix}-{code.lower()}-{self.mode}-{self.run_tag}"
            definition = build_agent_definition(self.deployment, system, tool=None if self.mode == "parity" else "gate")
            details = self.project.agents.create_version(name, definition=definition,
                                                         description=f"ClaimCrew {self.mode} {code}")
            self.created[code] = {"name": details.name, "version": str(details.version)}
        return self.created[code]

    def cleanup(self) -> None:
        for ref in self.created.values():
            try:
                self.project.agents.delete_version(ref["name"], ref["version"])
            except Exception as e:  # noqa: BLE001 — 清理失敗不應掩蓋量測結果
                print(f"[warn] 刪除 agent {ref} 失敗:{e}")
