"""產生 Document Intelligence custom neural 訓練樣本(診斷證明書)。

★ 格式聲明:labels.json / fields.json 之結構依訓練知識撰寫,
  **未經官方文件驗證**;Phase 1 首次訓練時以 Azure 真實 API 回應校正,
  若格式有出入,只需改本產生器重跑(來源真相在 claim.json + PDF)。

作法:欄位值不手標——以 pdfplumber 在渲染完成的 PDF 中搜尋欄位值的
實際座標,自動產出標註(值與座標必然一致)。座標正規化為 0~1。

執行:python3 -m app.datagen.di_labels [C### ...]  → 產生標註(不複製 PDF;無參數=全量,單目標支援)
      有任何「找不到值」警告即以非零退出(CI 可偵測標註漏失)
      python3 -m app.datagen.di_labels --assemble <dir>
                                                  → 把 PDF+標註組裝成可直接上傳 Blob 的目錄
輸出:data/di_training/{C###.pdf.labels.json, fields.json}
      (PDF 不重複入庫;來源為 data/claims/C###/pdf/diagnosis_certificate.pdf,
       上傳前以 --assemble 組裝——repo 只存「可重生的原料」)
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pdfplumber

from app.datagen.cases import CLAIMS_DIR

OUT_DIR = CLAIMS_DIR.parent / "di_training"

# DI 訓練欄位維持平面(custom neural 不標重複群組);多段住院之列表結構由
# Extraction Agent 自 PDF 文字整理(schema Admission),DI 只標第一段住院
FIELDS = [
    "patient_name",
    "diagnosis",
    "icd_code",
    "admission_date",
    "discharge_date",
    "treatment_summary",
    "issue_date",
    "hospital_name",
]

LABELS_SCHEMA = "https://schema.cognitiveservices.azure.com/formrecognizer/2021-03-01/labels.json"
FIELDS_SCHEMA = "https://schema.cognitiveservices.azure.com/formrecognizer/2021-03-01/fields.json"


Box = tuple[float, float, float, float]


def _bbox_entries(page: Any, value: str, used: set[Box] | None = None) -> list[dict[str, Any]]:
    """在頁面找出 value 的實際字元序列,回傳 labels 格式的 value 條目(座標 0~1 正規化)。

    以逐字座標比對(不用 page.search 的整串字串搜尋):跨行文字(治療摘要)會被切成
    「每一渲染行一個框」,拼接後仍等於原值——先前整串搜不到就退回標點切段,長摘要只標到
    第一句,71 份中 15 份如此,DI 模型忠實學到「只抽第一句」(2026-09-30 C008 真實觸發實測)。
    `used` 為同一份文件已標過的框:同值多次出現(出院日=開立日)依欄位順序取第一個未用者。
    """
    w, h = page.width, page.height
    used = set() if used is None else used
    chars = [c for c in page.chars if not str(c["text"]).isspace()]
    stream = "".join(str(c["text"]) for c in chars)
    needle = value.replace(" ", "")
    if not needle:
        return []

    def box_of(run: list[dict[str, Any]]) -> Box:
        return (
            round(min(c["x0"] for c in run) / w, 4), round(min(c["top"] for c in run) / h, 4),
            round(max(c["x1"] for c in run) / w, 4), round(max(c["bottom"] for c in run) / h, 4),
        )

    pos = 0
    while (i := stream.find(needle, pos)) >= 0:
        span = chars[i:i + len(needle)]
        lines: list[list[dict[str, Any]]] = [[span[0]]]
        for c in span[1:]:
            if abs(c["top"] - lines[-1][-1]["top"]) < 1.0:
                lines[-1].append(c)
            else:
                lines.append([c])
        first = box_of(lines[0])
        if first in used:
            pos = i + 1
            continue
        used.add(first)
        entries = []
        for run in lines:
            x0, top, x1, bottom = box_of(run)
            entries.append({
                "page": 1,
                "text": "".join(str(c["text"]) for c in run),
                "boundingBoxes": [[x0, top, x1, top, x1, bottom, x0, bottom]],
            })
        return entries
    return []


def generate(targets: list[str] | None = None) -> tuple[int, list[str]]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    n = 0
    case_dirs = [CLAIMS_DIR / t for t in targets] if targets else sorted(CLAIMS_DIR.iterdir())
    for case_dir in case_dirs:
        claim_file = case_dir / "claim.json"
        pdf_file = case_dir / "pdf" / "diagnosis_certificate.pdf"
        if not (claim_file.is_file() and pdf_file.is_file()):
            continue
        claim = json.loads(claim_file.read_text(encoding="utf-8"))
        doc = claim["documents"]["diagnosis_certificate"]
        labels = []
        used: set[Box] = set()  # 同一文件內框不得重複(欄位順序 = 頁面由上而下順序)
        with pdfplumber.open(pdf_file) as pdf:
            page = pdf.pages[0]
            first_adm = doc["admissions"][0] if doc["admissions"] else {}
            for field in FIELDS:
                value = first_adm.get(field, "") if field in ("admission_date", "discharge_date") else doc[field]
                if not value:
                    warnings.append(f"{claim['claim_id']}.{field}:文件無此值,未標註")
                    continue
                entries = _bbox_entries(page, value, used)
                if not entries:
                    warnings.append(f"{claim['claim_id']}.{field}:PDF 中找不到值,未標註")
                    continue
                labels.append({"label": field, "value": entries})
        (OUT_DIR / f"{claim['claim_id']}.pdf.labels.json").write_text(
            json.dumps(
                {"$schema": LABELS_SCHEMA, "document": f"{claim['claim_id']}.pdf", "labels": labels},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        n += 1
    (OUT_DIR / "fields.json").write_text(
        json.dumps(
            {
                "$schema": FIELDS_SCHEMA,
                "fields": [
                    {"fieldKey": f, "fieldType": "string", "fieldFormat": "not-specified"}
                    for f in FIELDS
                ],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return n, warnings


def assemble(target: Path) -> int:
    """組裝上傳目錄:PDF(自 data/claims 取)+ labels.json + fields.json 平鋪同層。"""
    target.mkdir(parents=True, exist_ok=True)
    n = 0
    for label_file in sorted(OUT_DIR.glob("C*.pdf.labels.json")):
        cid = label_file.name.split(".")[0]
        src_pdf = CLAIMS_DIR / cid / "pdf" / "diagnosis_certificate.pdf"
        if not src_pdf.is_file():
            print(f"跳過 {cid}:來源 PDF 不存在")
            continue
        shutil.copy(src_pdf, target / f"{cid}.pdf")
        shutil.copy(label_file, target / label_file.name)
        n += 1
    shutil.copy(OUT_DIR / "fields.json", target / "fields.json")
    return n


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--assemble":
        print(f"組裝 {assemble(Path(sys.argv[2]))} 份樣本至 {sys.argv[2]}")
        sys.exit(0)
    count, warns = generate(sys.argv[1:] or None)
    print(f"產出 {count} 份訓練樣本(需 ≥5)")
    for w in warns:
        print("  警告:", w)
    sys.exit(1 if warns else 0)
