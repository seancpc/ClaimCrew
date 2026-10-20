"""Azure OpenAI structured outputs 煙霧測試(SPEC 2026-10-01 行為 11;SPEC 掛號 ① 的校正點)。

以 `to_structured_output_schema(ClaimBundle)`(含 incident_statement 的 nullable anyOf、Decimal→string)
打一次真 API,要求模型用占位內容填滿 schema;成功條件:回應通過 Pydantic 驗證。
用法:
    $env:AOAI_KEY = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
    python tools/aoai_smoke.py
    python tools/aoai_smoke.py --entra      # SPEC-t:az login 身分,不用金鑰
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.calc import total_amount  # noqa: E402
from app.pipeline.aoai_client import AOAIClient  # noqa: E402
from app.schemas.claim import ClaimBundle, DecisionResult, to_structured_output_schema  # noqa: E402

SYSTEM = (
    "你是測試用的 JSON 產生器。依提供的 JSON Schema 產出一筆合法物件;"
    "所有字串欄位填中性占位符(如 <值>),數字字串填 \"0\",日期填 2000-01-01,信心填 0.5。"
)


def main() -> int:
    # SPEC-t T2:--entra 以 az login 身分(DefaultAzureCredential)呼叫,驗證無金鑰路徑
    # (需角色 Cognitive Services OpenAI User)
    if "--entra" in sys.argv[1:]:
        from app.pipeline.auth import entra_token_provider
        client = AOAIClient(token_provider=entra_token_provider())
        print("auth=entra(DefaultAzureCredential)")
    else:
        client = AOAIClient()
    for model in (ClaimBundle, DecisionResult):
        schema = to_structured_output_schema(model)
        t0 = time.time()
        resp = client.structured_call(system=SYSTEM, user=f"claim_id 填 C000。schema 名稱 {model.__name__}。",
                                      schema_name=model.__name__, schema=schema, max_completion_tokens=3000)
        obj = model.model_validate_json(resp.content)
        print(f"{model.__name__}: OK  model={resp.model} tokens={resp.prompt_tokens}+{resp.completion_tokens} "
              f"{time.time() - t0:.1f}s")
        if isinstance(obj, ClaimBundle):
            print(f"  claim_id={obj.claim_id};incident_statement(nullable anyOf)回傳型別:"
                  f"{type(obj.incident_statement).__name__}")
        elif isinstance(obj, DecisionResult):
            # B1:LLM 只填 calculation(含 enum kind 於陣列項目——SPEC 2026-10-01-b 高風險假設 1 的校正點)
            calc = obj.adjudication.calculation
            print(f"  decision={obj.adjudication.decision.value} calc_lines={len(calc)} "
                  f"kinds={[ln.kind.value for ln in calc]} program_total={total_amount(calc)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
