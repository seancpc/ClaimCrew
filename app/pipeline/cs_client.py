"""SPEC-v:Azure AI Content Safety 用戶端——groundedness 偵測 + 四類內容掃描(REST,urllib,同 DIClient)。

★ REST 細節依訓練知識撰寫,待桌機 tools/cs_smoke.py 真跑校正(假設 1:F0 可呼叫 detectGroundedness)。
驗證二擇一:`key`(Ocp-Apim-Subscription-Key)或 `token_provider`(Entra Bearer,scope 同 DI)。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.schemas.claim import GroundednessResult

GROUNDEDNESS_API = "2024-09-15-preview"
ANALYZE_API = "2024-09-01"
SHIELD_API = "2024-09-01"  # Prompt Shields:text:shieldPrompt(userPrompt + documents[];間接注入偵測)
TEXT_MAX_CHARS = 7_500  # detectGroundedness text 上限
SOURCES_MAX_CHARS = 55_000  # groundingSources 合計上限
HARM_CATEGORIES = ("Hate", "SelfHarm", "Sexual", "Violence")
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


class CSError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShieldResult:
    user_attack: bool
    document_attacks: list[bool]

    @property
    def any_attack(self) -> bool:
        return self.user_attack or any(self.document_attacks)


def clip_inputs(text: str, sources: list[str]) -> tuple[str, list[str], bool]:
    """text 截到 TEXT_MAX_CHARS;sources 合計超過 SOURCES_MAX_CHARS 時逐次丟掉最長者(保留原順序)。"""
    truncated = False
    if len(text) > TEXT_MAX_CHARS:
        text, truncated = text[:TEXT_MAX_CHARS], True
    kept = list(sources)
    while kept and sum(len(s) for s in kept) > SOURCES_MAX_CHARS:
        kept.remove(max(kept, key=len))
        truncated = True
    return text, kept, truncated


class CSClient:
    def __init__(self, endpoint: str, key: str = "", token_provider: Callable[[], str] | None = None) -> None:
        if not key and token_provider is None:
            raise ValueError("CSClient 需要 key(CS_KEY)或 token_provider(CLAIMCREW_AUTH=entra)")
        self._endpoint = endpoint.rstrip("/")
        self._key, self._token_provider = key, token_provider

    def _post(self, path: str, api_version: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._endpoint}/contentsafety/{path}?api-version={api_version}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last = ""
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if self._token_provider is not None:
                req.add_header("Authorization", f"Bearer {self._token_provider()}")
            else:
                req.add_header("Ocp-Apim-Subscription-Key", self._key)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return dict(json.loads(raw)) if raw else {}
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} POST {path}: {e.read().decode('utf-8', errors='replace')[:300]}"
                if e.code not in _RETRY_STATUS:
                    raise CSError(last) from e
            except urllib.error.URLError as e:
                last = f"連線失敗 POST {path}: {e.reason}"
            time.sleep(0.5 * (attempt + 1))
        raise CSError(f"重試 {_MAX_ATTEMPTS} 次仍失敗:{last}")

    def detect_groundedness(self, text: str, sources: list[str], domain: str = "Medical", *,
                            task: str = "Summarization", query: str | None = None) -> GroundednessResult:
        """task=Summarization 判整段;task=QnA 須帶 query(SPEC-v 第二段 E2:逐主張問答)。"""
        if task == "QnA" and not query:
            raise ValueError("task=QnA 必須提供 query")
        text, sources, truncated = clip_inputs(text, sources)
        body: dict[str, Any] = {"domain": domain, "task": task, "text": text,
                                "groundingSources": sources, "reasoning": False}
        if task == "QnA":
            body["qna"] = {"query": query}
        r = self._post("text:detectGroundedness", GROUNDEDNESS_API, body)
        return GroundednessResult(
            ungrounded=bool(r.get("ungroundedDetected", False)),
            percentage=Decimal(str(r.get("ungroundedPercentage", 0))),
            details=[str(d.get("text", "")) for d in r.get("ungroundedDetails", []) or []],
            truncated=truncated)

    def analyze_text(self, text: str) -> dict[str, int]:
        r = self._post("text:analyze", ANALYZE_API, {"text": text[:10_000], "categories": list(HARM_CATEGORIES)})
        got = {str(c.get("category")): int(c.get("severity", 0)) for c in r.get("categoriesAnalysis", []) or []}
        return {c: got.get(c, 0) for c in HARM_CATEGORIES}

    def shield_prompt(self, user_prompt: str, documents: list[str]) -> ShieldResult:
        """Prompt Shields:使用者提示的越獄 + 文件的間接注入(E3:DI 抽取文字當 documents)。"""
        r = self._post("text:shieldPrompt", SHIELD_API, {"userPrompt": user_prompt, "documents": documents})
        docs = r.get("documentsAnalysis", []) or []
        return ShieldResult(user_attack=bool((r.get("userPromptAnalysis") or {}).get("attackDetected", False)),
                            document_attacks=[bool(d.get("attackDetected", False)) for d in docs])
