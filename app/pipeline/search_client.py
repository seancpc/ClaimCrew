"""Azure AI Search(hybrid)與 Azure OpenAI embeddings 的 REST 用戶端(SPEC 2026-10-01;供 core+search 策略)。

REST 版本已於 2026-10-01 以 tools/search_index.py 真跑校正:Search 2024-07-01、embeddings 2024-10-21、
索引 policy-clauses(104 份條款級文件、1536 維 HNSW、zh-Hant 分析器)。金鑰只讀環境變數 SEARCH_KEY / AOAI_KEY。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from app.gate.policy_index import Clause

SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "https://srch-claimcrew.search.windows.net")
AOAI_ENDPOINT = os.environ.get("AOAI_ENDPOINT", "https://oai-claimcrew.openai.azure.com/")
SEARCH_API = "2024-07-01"
AOAI_API = "2024-10-21"
EMBED_DEPLOYMENT = "text-embedding-3-small"
EMBED_DIM = 1536
INDEX_NAME = "policy-clauses"


class SearchError(RuntimeError):
    pass


def _call(url: str, key_header: tuple[str, str], method: str = "GET", body: Any = None) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header(*key_header)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise SearchError(
            f"HTTP {e.code} {method} {url}\n{e.read().decode('utf-8', errors='replace')[:1500]}"
        ) from e


def _key(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SearchError(f"缺環境變數 {name}")
    return value


def search_rest(path: str, method: str = "GET", body: Any = None) -> Any:
    return _call(f"{SEARCH_ENDPOINT.rstrip('/')}{path}?api-version={SEARCH_API}", ("api-key", _key("SEARCH_KEY")),
                 method, body)


def embed(texts: list[str]) -> list[list[float]]:
    url = f"{AOAI_ENDPOINT.rstrip('/')}/openai/deployments/{EMBED_DEPLOYMENT}/embeddings?api-version={AOAI_API}"
    out: list[list[float]] = []
    for i in range(0, len(texts), 16):  # 批次 16,避開單請求 token 上限
        resp = _call(url, ("api-key", _key("AOAI_KEY")), "POST", {"input": texts[i:i + 16]})
        out.extend(d["embedding"] for d in sorted(resp["data"], key=lambda d: d["index"]))
    return out


def hybrid_search(query: str, policy: str | None, k: int) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "search": query,
        "vectorQueries": [{"kind": "vector", "vector": embed([query])[0], "fields": "content_vector", "k": k}],
        "top": k,
        "select": "id,policy_code,clause_no,title,content",
    }
    if policy:
        body["filter"] = f"policy_code eq '{policy}'"
    return list(search_rest(f"/indexes/{INDEX_NAME}/docs/search", "POST", body)["value"])


class SearchRetriever:
    """core+search 策略的檢索器:命中條款以條款庫的 Clause 回傳(原文以條款庫為準,索引只負責找)。"""

    def __init__(self, index: dict[str, dict[str, Clause]]):
        self._index = index

    def search(self, query: str, policy_code: str, k: int) -> list[Clause]:
        hits = hybrid_search(query, policy_code, k)
        out = []
        for h in hits:
            clause = self._index.get(h["policy_code"], {}).get(h["clause_no"])
            if clause is not None:
                out.append(clause)
        return out
