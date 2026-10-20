"""Azure OpenAI structured outputs 用戶端(SPEC 2026-10-01 行為 5、8)。

單一真相源:response_format 一律帶 `to_structured_output_schema()` 產出的 JSON schema(strict),
不讓 SDK 另行推導;回應由呼叫端以 Pydantic 驗證。SDK:openai==3.22.1(AzureOpenAI)。
模型:部署名 gpt-4o(版本 2024-11-20,Bicep 釘選、NoAutoUpgrade);回應 `model` 欄位記入 _run.jsonl。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

AOAI_API_VERSION = "2024-10-21"
DEFAULT_DEPLOYMENT = "gpt-4o"
DEFAULT_ENDPOINT = "https://oai-claimcrew.openai.azure.com/"


class TransientError(RuntimeError):
    """可重試錯誤(429 / 5xx / 連線逾時);429 附伺服器 Retry-After 秒數(無則 None)。"""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def retry_after_from_headers(headers: Mapping[str, str]) -> float | None:
    """由回應標頭取 Retry-After(秒)或 retry-after-ms;大小寫不敏感;無法解析回 None。"""
    lower = {k.lower(): v for k, v in headers.items()}
    try:
        if "retry-after" in lower:
            return float(lower["retry-after"])
        if "retry-after-ms" in lower:
            return float(lower["retry-after-ms"]) / 1000
    except (TypeError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class StructuredResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0  # SPEC-k:prompt 中命中 prompt cache 的部分(usage.prompt_tokens_details.cached_tokens)


class StructuredCaller(Protocol):
    def structured_call(self, *, system: str, user: str, schema_name: str, schema: dict[str, Any],
                        max_completion_tokens: int, deployment: str | None = None) -> StructuredResponse: ...


class AOAIClient:
    def __init__(self, endpoint: str | None = None, key: str | None = None,
                 deployment: str = DEFAULT_DEPLOYMENT, seed: int = 20260101,
                 token_provider: Callable[[], str] | None = None):
        import openai  # 延遲 import:測試以 fake 注入時不需 SDK

        self._deployment = deployment
        self._seed = seed
        resolved_endpoint: str = endpoint or os.environ.get("AOAI_ENDPOINT") or DEFAULT_ENDPOINT
        # SPEC-t T1:token_provider(Entra)與 api_key 二擇一;entra 模式不讀也不送 AOAI_KEY
        auth: dict[str, Any] = ({"azure_ad_token_provider": token_provider} if token_provider is not None
                                else {"api_key": key or os.environ["AOAI_KEY"]})
        self._client = openai.AzureOpenAI(
            **auth,
            api_version=AOAI_API_VERSION,
            azure_endpoint=resolved_endpoint,
            max_retries=0,  # 重試由 runner 統一控制並記錄
        )

    def structured_call(self, *, system: str, user: str, schema_name: str, schema: dict[str, Any],
                        max_completion_tokens: int, deployment: str | None = None) -> StructuredResponse:
        """deployment:SPEC-k 逐呼叫覆寫部署名(ADR-007 分層路由實測);None 用建構時的預設。"""
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        try:
            resp = self._client.chat.completions.create(
                model=deployment or self._deployment,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
                max_completion_tokens=max_completion_tokens,
                seed=self._seed,  # 決定論策略:seed + structured outputs(不以 temperature 為唯一手段)
            )
        except RateLimitError as e:
            headers = getattr(getattr(e, "response", None), "headers", None) or {}
            raise TransientError(str(e), retry_after_from_headers(headers)) from e
        except (APITimeoutError, APIConnectionError) as e:
            raise TransientError(str(e)) from e
        except APIStatusError as e:
            if e.status_code >= 500:
                raise TransientError(str(e)) from e
            raise
        choice = resp.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError(f"finish_reason={choice.finish_reason}(可能被 max_completion_tokens 截斷或被過濾)")
        usage = resp.usage
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        return StructuredResponse(
            content=choice.message.content or "",
            model=resp.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=(getattr(details, "cached_tokens", None) or 0) if details else 0,
        )
