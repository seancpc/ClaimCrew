"""SPEC-t T1:驗證模式切換——`CLAIMCREW_AUTH=key`(預設,桌機金鑰)或 `entra`(雲端 Job 託管身分 / 桌機 az login)。

entra 模式:DI 以 `Authorization: Bearer <token>`、AOAI 以 `azure_ad_token_provider`,scope 皆為 Cognitive Services;
token 由 azure-identity `DefaultAzureCredential` 取得(Container Apps Job 走系統指派身分,桌機走 az login)。
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from app.pipeline.aoai_client import AOAIClient
from app.pipeline.cs_client import CSClient
from app.pipeline.di_client import DIClient

AUTH_ENV = "CLAIMCREW_AUTH"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
TokenProvider = Callable[[], str]


def auth_mode() -> str:
    mode = os.environ.get(AUTH_ENV, "key")
    if mode not in ("key", "entra"):
        raise ValueError(f"{AUTH_ENV} 只接受 key | entra,收到 {mode!r}")
    return mode


def entra_token_provider(credential: Any | None = None) -> TokenProvider:
    """回傳每次呼叫取一枚 Cognitive Services token 的函式(SDK 會自行快取與續期)。"""
    if credential is None:
        from azure.identity import DefaultAzureCredential  # 延後 import:key 模式不需 azure-identity

        credential = DefaultAzureCredential()

    def provider() -> str:
        return str(credential.get_token(COGNITIVE_SCOPE).token)
    return provider


def make_di_client(endpoint: str) -> DIClient:
    if auth_mode() == "entra":
        return DIClient(endpoint, key="", token_provider=entra_token_provider())
    return DIClient(endpoint, key=os.environ.get("DI_KEY", ""))


def make_aoai_client(**kwargs: Any) -> AOAIClient:
    if auth_mode() == "entra":
        return AOAIClient(token_provider=entra_token_provider(), **kwargs)
    return AOAIClient(**kwargs)


def make_cs_client(endpoint: str) -> CSClient:
    """SPEC-v:Content Safety 用戶端;key 模式讀 CS_KEY,entra 用同一 Cognitive Services scope。"""
    if auth_mode() == "entra":
        return CSClient(endpoint, key="", token_provider=entra_token_provider())
    return CSClient(endpoint, key=os.environ.get("CS_KEY", ""))
