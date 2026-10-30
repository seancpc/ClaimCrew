"""DI custom neural 模型「真實觸發」煙霧測試(固定解法第 7 條:備援/新路徑必以真呼叫驗證)。

★ REST 細節依訓練知識撰寫(analyze 端點、輪詢標頭、回應欄位路徑),首次執行以真回應校正。

用法(桌機,venv 內;金鑰以環境變數傳入,禁止寫進檔案或 log):
    $env:DI_KEY = (az cognitiveservices account keys list -n di-claimcrew -g rg-claimcrew --query key1 -o tsv)
    python tools/di_smoke.py C008                       # 預設模型 diagnosis-cert-v3(同 DIAGNOSIS_MODEL_ID)
    python tools/di_smoke.py C008 --model diagnosis-cert-v2 --dump check\\di_C008.json
    python tools/di_smoke.py C008 --model diagnosis-cert-v3 --entra   # SPEC-t:az login 身分,不用金鑰

輸出:8 個欄位的 抽取值 / 信心 / 真值 / 是否一致 對照表;並列出版面文字中含「第2次」的列
(驗證多段住院的第二段有文字可供 Extraction Agent 整理)。任一欄位不一致以非零退出。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DIR = ROOT / "data" / "claims"
DEFAULT_ENDPOINT = "https://di-claimcrew.cognitiveservices.azure.com/"
API_VERSION = "2024-11-30"
FIELDS = [
    "patient_name", "diagnosis", "icd_code", "admission_date",
    "discharge_date", "treatment_summary", "issue_date", "hospital_name",
]


def _request(url: str, key: str, method: str = "GET", body: bytes | None = None,
             content_type: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    req = urllib.request.Request(url, data=body, method=method)
    if key.startswith("Bearer "):  # SPEC-t T2:--entra 以 Bearer token 取代金鑰標頭
        req.add_header("Authorization", key)
    else:
        req.add_header("Ocp-Apim-Subscription-Key", key)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return (json.loads(raw) if raw else {}), headers
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} {method} {url}\n{detail}") from e


def analyze(endpoint: str, key: str, model_id: str, pdf: Path) -> dict[str, Any]:
    url = f"{endpoint.rstrip('/')}/documentintelligence/documentModels/{model_id}:analyze?api-version={API_VERSION}"
    _, headers = _request(url, key, "POST", pdf.read_bytes(), "application/pdf")
    op = headers.get("operation-location")
    if not op:
        raise SystemExit(f"回應缺 Operation-Location 標頭:{headers}")
    for _ in range(60):  # F0 較慢,最多等 2 分鐘
        time.sleep(2)
        result, _ = _request(op, key)
        status = result.get("status")
        if status == "succeeded":
            return result
        if status in ("failed", "canceled"):
            raise SystemExit(f"分析失敗:{json.dumps(result, ensure_ascii=False)[:800]}")
    raise SystemExit("輪詢逾時(120 秒)")


def expected_values(case_id: str) -> dict[str, str]:
    claim = json.loads((CLAIMS_DIR / case_id / "claim.json").read_text(encoding="utf-8"))
    doc = claim["documents"]["diagnosis_certificate"]
    first = doc["admissions"][0] if doc["admissions"] else {}
    return {
        f: (first.get(f, "") if f in ("admission_date", "discharge_date") else doc.get(f, ""))
        for f in FIELDS
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--model", default="diagnosis-cert-v3")
    ap.add_argument("--endpoint", default=os.environ.get("DI_ENDPOINT", DEFAULT_ENDPOINT))
    ap.add_argument("--dump", help="把完整回應存成 JSON(放 check/,勿入版控)")
    ap.add_argument("--entra", action="store_true", help="SPEC-t:以 az login 身分(DefaultAzureCredential)呼叫,不用金鑰")
    args = ap.parse_args()

    if args.entra:
        sys.path.insert(0, str(ROOT))
        from app.pipeline.auth import entra_token_provider
        key = "Bearer " + entra_token_provider()()
        print("auth=entra(DefaultAzureCredential)")
    else:
        key = os.environ.get("DI_KEY") or ""
    if not key:
        print("缺環境變數 DI_KEY(見檔頭用法),或改用 --entra", file=sys.stderr)
        return 2
    pdf = CLAIMS_DIR / args.case_id / "pdf" / "diagnosis_certificate.pdf"
    if not pdf.is_file():
        print(f"找不到 {pdf}", file=sys.stderr)
        return 2

    t0 = time.time()
    result = analyze(args.endpoint, key, args.model, pdf)
    elapsed = time.time() - t0
    if args.dump:
        Path(args.dump).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    analyze_result = result.get("analyzeResult", {})
    docs = analyze_result.get("documents", [])
    fields: dict[str, Any] = docs[0].get("fields", {}) if docs else {}
    expected = expected_values(args.case_id)

    head = docs[0] if docs else {}
    print(f"case={args.case_id} model={args.model} api={API_VERSION} 耗時 {elapsed:.1f}s "
          f"docType={head.get('docType')} docConfidence={head.get('confidence')}")
    print(f"{'欄位':<20}{'抽取值':<32}{'信心':>6}  {'真值':<32}一致")
    mismatches = 0
    for f in FIELDS:
        v = fields.get(f) or {}
        got = str(v.get("valueString", v.get("content", "")) or "")
        conf = v.get("confidence")
        exp = expected[f]
        ok = got.replace(" ", "") == exp.replace(" ", "")
        mismatches += 0 if ok else 1
        conf_s = f"{conf:.2f}" if isinstance(conf, int | float) else "  -"
        print(f"{f:<20}{got[:30]:<32}{conf_s:>6}  {exp[:30]:<32}{'✓' if ok else '✗'}")

    lines = [ln.get("content", "") for pg in analyze_result.get("pages", []) for ln in pg.get("lines", [])]
    second = [ln for ln in lines if "第2次" in ln or "第 2 次" in ln]
    print(f"\n版面文字列數 {len(lines)};含「第2次」的列:{second if second else '無'}")
    print(f"\n不一致欄位數:{mismatches}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
