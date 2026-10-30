"""SPEC-t T1:無金鑰(Entra)驗證路徑——DI 以 Bearer token、AOAI 以 azure_ad_token_provider;金鑰模式位元組不變。"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.pipeline import auth, di_client


class _Resp:
    def __init__(self, body: dict[str, Any], headers: dict[str, str] | None = None):
        self._body, self.headers, self.status = json.dumps(body).encode(), headers or {}, 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, responses: list[_Resp]) -> list[Any]:
    seen: list[Any] = []

    def fake_urlopen(req, timeout=0):
        seen.append(req)
        return responses.pop(0)
    monkeypatch.setattr(di_client.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_t1_auth_mode_defaults_to_key_and_reads_env(monkeypatch):
    monkeypatch.delenv(auth.AUTH_ENV, raising=False)
    assert auth.auth_mode() == "key"
    monkeypatch.setenv(auth.AUTH_ENV, "entra")
    assert auth.auth_mode() == "entra"
    monkeypatch.setenv(auth.AUTH_ENV, "bogus")
    with pytest.raises(ValueError):
        auth.auth_mode()


def test_t1_di_client_entra_sends_bearer_and_no_key_header(monkeypatch, tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    seen = _capture(monkeypatch, [_Resp({}, {"operation-location": "https://op"}),
                                  _Resp({"status": "succeeded", "analyzeResult": {}})])
    c = di_client.DIClient("https://di", key="", token_provider=lambda: "tok-123", poll_s=0)
    c.analyze_pdf("prebuilt-layout", pdf)
    for req in seen:
        assert req.get_header("Authorization") == "Bearer tok-123"
        assert not req.has_header("Ocp-apim-subscription-key")


def test_t1_di_client_key_mode_unchanged(monkeypatch, tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    seen = _capture(monkeypatch, [_Resp({}, {"operation-location": "https://op"}),
                                  _Resp({"status": "succeeded", "analyzeResult": {}})])
    di_client.DIClient("https://di", key="k1", poll_s=0).analyze_pdf("prebuilt-layout", pdf)
    for req in seen:
        assert req.get_header("Ocp-apim-subscription-key") == "k1" and not req.has_header("Authorization")


def test_t1_di_client_requires_key_or_token():
    with pytest.raises(ValueError):
        di_client.DIClient("https://di", key="")


def test_t1_aoai_client_entra_uses_token_provider_not_api_key(monkeypatch):
    import openai

    from app.pipeline.aoai_client import AOAIClient
    captured: dict[str, Any] = {}

    class FakeAzureOpenAI:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureOpenAI)
    monkeypatch.delenv("AOAI_KEY", raising=False)
    AOAIClient(token_provider=lambda: "tok")
    assert "api_key" not in captured and callable(captured["azure_ad_token_provider"])
    assert captured["azure_ad_token_provider"]() == "tok"
    captured.clear()
    AOAIClient(key="k1")
    assert captured["api_key"] == "k1" and "azure_ad_token_provider" not in captured


def test_t1_entra_token_provider_uses_cognitive_scope():
    calls: list[str] = []

    class FakeCred:
        def get_token(self, scope: str):
            calls.append(scope)
            return type("T", (), {"token": "abc"})()
    tp = auth.entra_token_provider(FakeCred())
    assert tp() == "abc" and calls == [auth.COGNITIVE_SCOPE]


def test_t1_factories_pick_mode(monkeypatch):
    import openai

    from app.pipeline.aoai_client import AOAIClient

    class FakeAzureOpenAI:
        def __init__(self, **kw):
            self.kw = kw
    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureOpenAI)
    monkeypatch.setenv(auth.AUTH_ENV, "entra")
    monkeypatch.setattr(auth, "entra_token_provider", lambda cred=None: (lambda: "t"))
    di = auth.make_di_client("https://di")
    assert isinstance(di, di_client.DIClient) and di._token_provider is not None
    ao = auth.make_aoai_client()
    assert isinstance(ao, AOAIClient) and "azure_ad_token_provider" in ao._client.kw
    monkeypatch.setenv(auth.AUTH_ENV, "key")
    monkeypatch.setenv("DI_KEY", "dk")
    monkeypatch.setenv("AOAI_KEY", "ak")
    assert auth.make_di_client("https://di")._key == "dk"
    assert auth.make_aoai_client()._client.kw["api_key"] == "ak"
