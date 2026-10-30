"""Document Intelligence 用戶端與 prompt 載荷整理(SPEC 2026-10-01 行為 4)。

- 診斷證明書:custom neural `diagnosis-cert-v3`(8 個平面欄位 + 信心;2026-09-30 C008/C055 真實觸發 8/8)
- 其他文件:`prebuilt-layout` 版面文字(行 + 表格列),由呼叫 1 的模型整理為結構(方案 b 基線)
- 缺席文件不呼叫、不出現在載荷(Extraction prompt 規則 8)
★ REST 細節(2024-11-30)已於 tools/di_smoke.py 真跑校正;layout 的 tables 結構依訓練知識,首跑驗證。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.pipeline.intake import OPTIONAL_DOCUMENTS, REQUIRED_DOCUMENTS, pdf_path

DI_API_VERSION = "2024-11-30"
DIAGNOSIS_MODEL_ID = "diagnosis-cert-v3"
LAYOUT_MODEL_ID = "prebuilt-layout"
DIAGNOSIS_FIELDS = (
    "patient_name", "diagnosis", "icd_code", "admission_date",
    "discharge_date", "treatment_summary", "issue_date", "hospital_name",
)


class DocumentAnalyzer(Protocol):
    def analyze_pdf(self, model_id: str, pdf: Path) -> dict[str, Any]:
        """回傳 {"fields": {name: {"value", "confidence"}}, "lines": [str, ...]}。"""
        ...


class DIError(RuntimeError):
    pass


class DIClient:
    """呼叫 DI analyze 並輪詢;只回傳整理後的 fields / lines,不回傳整份原始 JSON。

    驗證二擇一(SPEC-t T1):`key`(Ocp-Apim-Subscription-Key)或 `token_provider`(Entra Bearer,雲端 Job 託管身分)。
    """

    def __init__(self, endpoint: str, key: str, poll_s: float = 2.0, max_polls: int = 60,
                 token_provider: Callable[[], str] | None = None):
        if not key and token_provider is None:
            raise ValueError("DIClient 需要 key(DI_KEY)或 token_provider(CLAIMCREW_AUTH=entra)")
        self._endpoint = endpoint.rstrip("/")
        self._key = key
        self._token_provider = token_provider
        self._poll_s, self._max_polls = poll_s, max_polls

    def _request(self, url: str, method: str = "GET", body: bytes | None = None,
                 content_type: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        req = urllib.request.Request(url, data=body, method=method)
        if self._token_provider is not None:
            req.add_header("Authorization", f"Bearer {self._token_provider()}")
        else:
            req.add_header("Ocp-Apim-Subscription-Key", self._key)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return (json.loads(raw) if raw else {}), {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            raise DIError(f"HTTP {e.code} {method} {url}: {e.read().decode('utf-8', errors='replace')[:500]}") from e
        except urllib.error.URLError as e:  # 連線/代理層錯誤(非 HTTP 狀態碼)
            raise DIError(f"連線失敗 {method} {url}: {e.reason}") from e

    def analyze_pdf(self, model_id: str, pdf: Path) -> dict[str, Any]:
        url = f"{self._endpoint}/documentintelligence/documentModels/{model_id}:analyze?api-version={DI_API_VERSION}"
        _, headers = self._request(url, "POST", pdf.read_bytes(), "application/pdf")
        op = headers.get("operation-location")
        if not op:
            raise DIError("回應缺 Operation-Location 標頭")
        for _ in range(self._max_polls):
            time.sleep(self._poll_s)
            result, _ = self._request(op, "GET")
            status = result.get("status")
            if status == "succeeded":
                return normalize_analyze_result(result.get("analyzeResult", {}))
            if status in ("failed", "canceled"):
                raise DIError(f"分析失敗:{json.dumps(result, ensure_ascii=False)[:500]}")
        raise DIError("輪詢逾時")


class CachedAnalyzer:
    """DI 結果磁碟快取(2026-10-01 F0 月額度用罄):同一 PDF 內容 × 同一模型的分析結果為確定性,只抽一次。

    鍵 = sha256(PDF 位元組) ,目錄依模型 ID 分層(換模型版本不吃舊結果);檔名雜湊不含路徑(資料重生成改名仍命中)。
    壞檔/不可解析視為未命中並覆寫。快取放 check/(gitignore,拆建可丟)。
    """

    def __init__(self, inner: DocumentAnalyzer, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self.hits = 0
        self.misses = 0

    def _path(self, model_id: str, pdf_bytes: bytes) -> Path:
        return self._dir / model_id / f"{hashlib.sha256(pdf_bytes).hexdigest()}.json"

    def analyze_pdf(self, model_id: str, pdf: Path) -> dict[str, Any]:
        data = pdf.read_bytes()
        path = self._path(model_id, data)
        if path.is_file():
            try:
                result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return result
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        self.misses += 1
        result = self._inner.analyze_pdf(model_id, pdf)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return result


def _min_conf(spans: list[dict[str, Any]], words: list[dict[str, Any]]) -> float:
    """以 spans(offset/length)對應 words,取最低字詞信心;無對應字詞時回 1.0(無證據不扣分)。"""
    confs = []
    for sp in spans:
        lo, hi = int(sp.get("offset", 0)), int(sp.get("offset", 0)) + int(sp.get("length", 0))
        for w in words:
            ws = w.get("span") or {}
            o = int(ws.get("offset", -1))
            if lo <= o < hi:
                confs.append(float(w.get("confidence", 1.0)))
    return round(min(confs), 4) if confs else 1.0


def normalize_analyze_result(analyze_result: dict[str, Any]) -> dict[str, Any]:
    """custom 模型:documents[0].fields → {name: {value, confidence}};
    layout:每行 {text, confidence}(信心 = 行內最低字詞信心)+ 表格列(信心 = 列內儲存格最低字詞信心)。"""
    fields: dict[str, dict[str, Any]] = {}
    docs = analyze_result.get("documents") or []
    if docs:
        for name, f in (docs[0].get("fields") or {}).items():
            fields[name] = {
                "value": str(f.get("valueString", f.get("content", "")) or ""),
                "confidence": float(f.get("confidence") or 0.0),
            }
    words = [w for pg in analyze_result.get("pages", []) for w in pg.get("words", [])]
    lines: list[dict[str, Any]] = []
    for pg in analyze_result.get("pages", []):
        for ln in pg.get("lines", []):
            lines.append({"text": str(ln.get("content", "")), "confidence": _min_conf(ln.get("spans", []), words)})
    for table in analyze_result.get("tables") or []:
        rows: dict[int, dict[int, tuple[str, float]]] = {}
        for cell in table.get("cells", []):
            row = rows.setdefault(int(cell.get("rowIndex", 0)), {})
            conf = _min_conf(cell.get("spans", []), words)
            row[int(cell.get("columnIndex", 0))] = (str(cell.get("content", "")), conf)
        for r in sorted(rows):
            cells = [rows[r][c] for c in sorted(rows[r])]
            text = "表格列:" + " | ".join(t for t, _ in cells)
            lines.append({"text": text, "confidence": min(cf for _, cf in cells)})
    return {"fields": fields, "lines": lines}


def extract_case(case_dir: Path, analyzer: DocumentAnalyzer) -> dict[str, dict[str, Any]]:
    """對存在的文件逐份呼叫 DI;診斷證明書用 custom 模型,其餘 layout。缺席文件不呼叫。"""
    results: dict[str, dict[str, Any]] = {}
    for key, _ in (*REQUIRED_DOCUMENTS, *OPTIONAL_DOCUMENTS):
        pdf = pdf_path(case_dir, key)
        if not pdf.is_file():
            continue
        model = DIAGNOSIS_MODEL_ID if key == "diagnosis_certificate" else LAYOUT_MODEL_ID
        results[key] = analyzer.analyze_pdf(model, pdf)
    return results


def di_results_to_prompt_payload(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """呼叫 1 的使用者訊息載荷:只含存在的文件;診斷證明書帶欄位+信心,其他帶版面行。"""
    payload: dict[str, Any] = {}
    for key, res in results.items():
        lines = [ln if isinstance(ln, dict) else {"text": str(ln), "confidence": 1.0} for ln in res.get("lines", [])]
        entry: dict[str, Any] = {"lines": lines}
        if key == "diagnosis_certificate":
            fields = res.get("fields", {})
            entry["fields"] = {f: fields.get(f, {"value": "", "confidence": 0.0}) for f in DIAGNOSIS_FIELDS}
        payload[key] = entry
    return payload
