"""將案件 claim.json 渲染為三件套仿真 PDF;將保單 .md 渲染為條款 PDF。

- 執行:python3 -m app.datagen.render_pdf [claim_id ...](無參數=全部案件+保單;
  單目標執行支援:內部開發規則 固定解法 11)
- 輸出:data/claims/C###/pdf/{diagnosis_certificate,medical_receipt,claim_application}.pdf
        data/policies/pdf/POL-X.pdf
- 既有 PDF 會重新生成(渲染是純函數,來源真相在 claim.json / POL-X.md)
- 字型:嵌入式 CJK TTF(候選清單見 _FONT_CANDIDATES);排版仿真但不追求華麗(SPEC §12)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.datagen.cases import CLAIMS_DIR

POLICIES_DIR = CLAIMS_DIR.parent / "policies"

# 字型:優先嵌入 TTF(文字層帶正確 ToUnicode,抽取/DI 皆可回讀);
# CID 字型僅為最後備援——其文字層無 ToUnicode,抽回為亂碼(2026-08-31 實測)
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # 雲端容器(Debian)
    "C:/Windows/Fonts/msjh.ttc",  # Windows 微軟正黑(桌機)
]


def _register_font() -> str:
    from reportlab.pdfbase.ttfonts import TTFont

    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=0))
            return "CJK"
    print("警告:找不到可嵌入之 CJK TTF,退用 CID 字型(文字層抽取將為亂碼)")
    pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
    return "MSung-Light"


FONT = _register_font()

TITLE = ParagraphStyle("title", fontName=FONT, fontSize=16, alignment=1, spaceAfter=6)
SUBTITLE = ParagraphStyle("subtitle", fontName=FONT, fontSize=9, alignment=1, textColor=colors.grey)
BODY = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=16)
SMALL = ParagraphStyle("small", fontName=FONT, fontSize=8, leading=11, textColor=colors.grey)

SYNTHETIC_NOTE = "本文件為合成示範資料(ClaimCrew 專案),非真實文件"


def _doc(path: Path) -> SimpleDocTemplate:
    # invariant=1:固定 PDF 內部時間戳與文件 ID,重渲染位元組穩定(避免無意義 git diff)
    return SimpleDocTemplate(
        str(path), pagesize=A4, invariant=1,
        leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
    )


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    t = Table([[Paragraph(k, BODY), Paragraph(v, BODY)] for k, v in rows],
              colWidths=[42 * mm, 118 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.93, 0.93, 0.93)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _admission_rows(admissions: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """住院期間列:單段不加編號(DI 平面欄位標籤相容);多段以「第N次」前綴。"""
    rows: list[tuple[str, str]] = []
    for n, a in enumerate(admissions, 1):
        pre = f"第{n}次" if len(admissions) > 1 else ""
        rows += [(f"{pre}入院日期", a["admission_date"]), (f"{pre}出院日期", a["discharge_date"]),
                 (f"{pre}加護病房日數", a["icu_days"])]
    return rows


def render_diagnosis(doc_data: dict[str, Any], hospital: str, out: Path) -> None:
    story = [
        Paragraph(hospital, TITLE),
        Paragraph("診 斷 證 明 書", TITLE),
        Paragraph(SYNTHETIC_NOTE, SUBTITLE),
        Spacer(1, 8),
        _kv_table([
            ("病患姓名", doc_data["patient_name"]),
            ("診斷", doc_data["diagnosis"]),
            ("國際疾病分類碼", doc_data["icd_code"]),
            *_admission_rows(doc_data["admissions"]),
            ("治療摘要", doc_data["treatment_summary"]),
        ]),
        Spacer(1, 14),
        Paragraph("以上病患經本院診治屬實,特立此證明。", BODY),
        Spacer(1, 20),
        _kv_table([("開立日期", doc_data["issue_date"]), ("醫師簽章", "(簽章)")]),
    ]
    _doc(out).build(story)


def render_receipt(doc_data: dict[str, Any], out: Path) -> None:
    item_rows = [["項目", "金額(新臺幣元)"]] + [
        [i["item_name"], f'{int(i["amount"]):,}'] for i in doc_data["items"]
    ] + [["總計", f'{int(doc_data["total_amount"]):,}']]
    items = Table(
        [[Paragraph(c, BODY) for c in row] for row in item_rows],
        colWidths=[100 * mm, 60 * mm],
    )
    items.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
        ("BACKGROUND", (0, -1), (-1, -1), colors.Color(0.96, 0.96, 0.96)),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story = [
        Paragraph(doc_data["hospital_name"], TITLE),
        Paragraph("醫 療 費 用 收 據", TITLE),
        Paragraph(SYNTHETIC_NOTE, SUBTITLE),
        Spacer(1, 8),
        _kv_table([
            ("病患姓名", doc_data["patient_name"]),
            ("收據日期", doc_data["receipt_date"]),
            ("收據種類", "影本(蓋「與正本相符」戳記;正本已用於申領其他保險給付)" if doc_data["is_copy"] else "正本"),
        ]),
        Spacer(1, 10),
        items,
        Spacer(1, 14),
        Paragraph("本收據為正本,塗改無效。", SMALL),
    ]
    _doc(out).build(story)


_OTHER_INSURER_LABELS = [("room_fee", "病房費用"), ("surgery_fee", "手術費用"), ("misc_fee", "住院雜費"),
                         ("ambulance_fee", "救護車費用"), ("unallocated", "未分項總額")]


def _other_insurer_rows(paid: dict[str, str]) -> list[tuple[str, str]]:
    """申請書「是否已向其他保險公司申請」欄:未申請一列;已申請則逐桶列出非零金額。"""
    nonzero = [(label, int(paid[key])) for key, label in _OTHER_INSURER_LABELS if int(paid[key]) > 0]
    if not nonzero:
        return [("是否已向其他保險公司申請", "否")]
    return [("是否已向其他保險公司申請", "是")] + [(f"已獲他保理賠金額—{label}", f"{amt:,}") for label, amt in nonzero]


_INCIDENT_TITLES = {
    "police_record": ("警察機關道路交通事故／案件紀錄摘要", "警察機關"),
    "patient_statement": ("事故經過聲明書", "聲明人"),
    "accident_certificate": ("事故證明書", "開立單位"),
}


def render_incident(doc_data: dict[str, Any], out: Path) -> None:
    title, issuer_label = _INCIDENT_TITLES[doc_data["source"]]
    story = [
        Paragraph(title, TITLE),
        Paragraph(SYNTHETIC_NOTE, SUBTITLE),
        Spacer(1, 8),
        _kv_table([
            ("事故日期", doc_data["incident_date"]),
            ("事故經過", doc_data["statement_text"]),
        ]),
        Spacer(1, 20),
        _kv_table([(issuer_label, "(簽章)")]),
    ]
    _doc(out).build(story)


def render_application(doc_data: dict[str, Any], out: Path) -> None:
    story = [
        Paragraph("安心人壽保險股份有限公司", TITLE),
        Paragraph("理 賠 申 請 書", TITLE),
        Paragraph(SYNTHETIC_NOTE, SUBTITLE),
        Spacer(1, 8),
        _kv_table([
            ("申請人姓名", doc_data["applicant_name"]),
            ("保單號碼", doc_data["policy_number"]),
            ("保單生效日", doc_data["policy_effective_date"]),
            ("申請給付類型", doc_data["claim_type"]),
            ("申請日期", doc_data["application_date"]),
            ("申請理賠金額(新臺幣元)", f'{int(doc_data["claimed_amount"]):,}'),
            *_other_insurer_rows(doc_data["other_insurer_paid"]),
        ]),
        Spacer(1, 14),
        Paragraph("茲聲明本申請書所填各項均屬事實,如有虛偽願負法律責任。", BODY),
        Spacer(1, 20),
        _kv_table([("申請人簽章", "(簽章)")]),
    ]
    _doc(out).build(story)


def render_policy(md_path: Path, out: Path) -> None:
    """條款 .md → PDF:標題/條文逐段渲染(表格列以縮排文字呈現)。"""
    story: list[Any] = []
    for line in md_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith((">", "---")):
            continue
        if line.startswith("# "):
            story.append(Paragraph(line[2:], TITLE))
            story.append(Paragraph(SYNTHETIC_NOTE, SUBTITLE))
            story.append(Spacer(1, 8))
        elif line.startswith("## "):
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>{line[3:]}</b>", BODY))
        elif line.startswith("**") and line.endswith("**"):
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"<b>{line.strip('*')}</b>", BODY))
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= {"-", " ", ":"}:
                continue
            story.append(Paragraph("　" + "  /  ".join(cells), BODY))
        else:
            story.append(Paragraph(line, BODY))
    _doc(out).build(story)


def render_case(case_dir: Path) -> list[Path]:
    claim = json.loads((case_dir / "claim.json").read_text(encoding="utf-8"))
    docs = claim["documents"]
    pdf_dir = case_dir / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    written = []
    if docs["diagnosis_certificate"] is not None:
        p = pdf_dir / "diagnosis_certificate.pdf"
        render_diagnosis(docs["diagnosis_certificate"], docs["diagnosis_certificate"]["hospital_name"], p)
        written.append(p)
    if docs["medical_receipt"] is not None:
        p = pdf_dir / "medical_receipt.pdf"
        render_receipt(docs["medical_receipt"], p)
        written.append(p)
    if docs["claim_application"] is not None:
        p = pdf_dir / "claim_application.pdf"
        render_application(docs["claim_application"], p)
        written.append(p)
    if docs.get("incident_statement") is not None:
        p = pdf_dir / "incident_statement.pdf"
        render_incident(docs["incident_statement"], p)
        written.append(p)
    return written


def main(argv: list[str]) -> int:
    targets = argv or sorted(d.name for d in CLAIMS_DIR.iterdir() if d.is_dir())
    n = 0
    for cid in targets:
        case_dir = CLAIMS_DIR / cid
        if not (case_dir / "claim.json").is_file():
            print(f"跳過 {cid}:無 claim.json")
            continue
        n += len(render_case(case_dir))
    if not argv:  # 全量模式才連保單一起渲染
        pol_pdf_dir = POLICIES_DIR / "pdf"
        pol_pdf_dir.mkdir(exist_ok=True)
        for md in sorted(POLICIES_DIR.glob("POL-*.md")):
            render_policy(md, pol_pdf_dir / f"{md.stem}.pdf")
            n += 1
    print(f"渲染 {n} 個 PDF")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
