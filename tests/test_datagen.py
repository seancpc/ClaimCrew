"""app/datagen 直接單元測試(稽核 C25):金額計算器手算表、PDF 渲染文字層回讀、DI 標註座標。

金額手算值依 data/policies/POL-*.md 條文與「含入出院日」慣例逐一算出,不從計算器反推。
"""

import json
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest

from app.datagen import di_labels
from app.datagen.cases import CASES, all_cases, generate
from app.datagen.render_pdf import render_case, render_policy
from app.datagen.variants import _days, _pol_a_amount, _pol_b_amount, _pol_c_amount


def _write_claim(case_dir: Path, case: dict) -> None:
    case_dir.mkdir()
    (case_dir / "claim.json").write_text(json.dumps({"documents": case["documents"]}, ensure_ascii=False),
                                         encoding="utf-8")


# --- 日數慣例 ---
@pytest.mark.parametrize("adm,dis,expected", [
    ("2026-03-02", "2026-03-02", 1),   # 當日入出院亦計 1 日
    ("2026-03-02", "2026-03-07", 6),
    ("2026-01-05", "2026-02-24", 51),  # C008 第一段
])
def test_days_inclusive(adm, dis, expected):
    assert _days(adm, dis) == expected


# --- POL-A:日額 2000 / ICU 4000(不重複計日額)/ 輔助 1000 / 手術 10000·25000·50000 / 同次上限 90 日 ---
@pytest.mark.parametrize("kwargs,expected", [
    ({"days": 6}, 2000 * 6 + 1000 * 6),
    ({"days": 14, "icu_days": 4}, 2000 * 10 + 4000 * 4 + 1000 * 14),
    ({"days": 5, "surgery_level": 2}, 2000 * 5 + 1000 * 5 + 25000),
    ({"days": 97}, (2000 + 1000) * 90),               # C008:合併 97 日截 90
    ({"days": 90, "icu_days": 90, "surgery_level": 3}, (4000 + 1000) * 90 + 50000),
])
def test_pol_a_amount(kwargs, expected):
    amount, deriv = _pol_a_amount(**kwargs)
    assert amount == expected
    assert deriv.endswith(f"= {expected}")
    if kwargs.get("icu_days"):
        assert "不重複計日額" in deriv
    if kwargs["days"] > 90:
        assert "上限認列 90 日" in deriv


# --- POL-B:手術 5000/15000/30000/60000/100000;門診 50%;慰問 1000/日上限 30 日 ---
@pytest.mark.parametrize("args,expected", [
    ((2, True), 7500),
    ((1, True), 2500),
    ((3, False, 5), 30000 + 1000 * 5),
    ((5, False, 37), 100000 + 1000 * 30),             # 慰問上限 30 日
])
def test_pol_b_amount(args, expected):
    amount, deriv = _pol_b_amount(*args)
    assert amount == expected
    assert ("第8條" in deriv) == args[1]


# --- POL-C:病房 min(實際, 3000×日)/ 手術 80000×25%·50%·100% / 雜費 ≤60000 / 救護車 ≤2000 / 非健保 65% ---
def test_pol_c_amount_caps_each_item_independently():
    amount, deriv = _pol_c_amount(days=4, room=20000, surgery_fee=50000, surgery_level=2,
                                  misc=70000, ambulance=3500)
    assert amount == 12000 + 40000 + 60000 + 2000
    assert deriv.count("min(") == 4


def test_pol_c_amount_non_nhi_applies_65_percent_after_caps():
    amount, deriv = _pol_c_amount(days=2, room=5000, surgery_fee=0, surgery_level=None, misc=10000, nhi=False)
    assert amount == int(Decimal(5000 + 10000) * Decimal("0.65"))
    assert "×65%" in deriv


# --- 生成器 --force 防護與 PDF 文字層 ---
def test_generate_skips_existing_without_force(tmp_path: Path):
    first = generate(tmp_path)
    assert len(first) == 146  # 73 案 × (claim + truth)(SPEC-y 新增 C073)
    assert generate(tmp_path) == []          # 預設不覆蓋
    assert len(generate(tmp_path, force=True)) == 146


def test_render_case_text_layer_is_extractable(tmp_path: Path):
    """D3.5 回歸:嵌入 TTF 後文字層必須能抽回中文與數字(CID 字型會得亂碼)。"""
    case = all_cases()[0]  # 經預設值補齊(is_copy / other_insurer_paid)
    case_dir = tmp_path / case["claim_id"]
    _write_claim(case_dir, case)
    written = render_case(case_dir)
    assert [p.name for p in written] == ["diagnosis_certificate.pdf", "medical_receipt.pdf", "claim_application.pdf"]
    dc = case["documents"]["diagnosis_certificate"]
    with pdfplumber.open(written[0]) as pdf:
        text = pdf.pages[0].extract_text()
    assert "診 斷 證 明 書" in text
    for key in ("patient_name", "diagnosis", "icd_code"):
        assert dc[key] in text
    assert dc["admissions"][0]["admission_date"] in text
    assert "合成示範資料" in text


def test_render_policy_produces_pdf(tmp_path: Path):
    md = tmp_path / "POL-X.md"
    md.write_text(
        "# 測試保單\n\n## 第一章\n\n**第一條(測試)**\n內文。\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
        encoding="utf-8",
    )
    out = tmp_path / "POL-X.pdf"
    render_policy(md, out)
    with pdfplumber.open(out) as pdf:
        text = pdf.pages[0].extract_text()
    assert "第一條(測試)" in text and "1 / 2" in text


# --- DI 標註:座標正規化 0~1、跨行長文字切段 ---
def test_bbox_entries_normalized_and_segmented(tmp_path: Path):
    case = all_cases()[0]  # 經預設值補齊(is_copy / other_insurer_paid)
    case_dir = tmp_path / case["claim_id"]
    _write_claim(case_dir, case)
    pdf_path = render_case(case_dir)[0]
    dc = case["documents"]["diagnosis_certificate"]
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        single = di_labels._bbox_entries(page, dc["patient_name"])
        summary = di_labels._bbox_entries(page, dc["treatment_summary"])
        missing = di_labels._bbox_entries(page, "此字串不在頁面上")
    assert len(single) == 1 and single[0]["page"] == 1
    box = single[0]["boundingBoxes"][0]
    assert len(box) == 8 and all(0.0 <= v <= 1.0 for v in box)
    assert box[0] < box[2] and box[1] < box[5]
    assert summary, "長文字須整串命中或切段命中"
    assert missing == []


def test_di_labels_generate_single_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = CASES[0]
    claims = tmp_path / "claims"
    generate(claims)
    render_case(claims / case["claim_id"])
    monkeypatch.setattr(di_labels, "CLAIMS_DIR", claims)
    monkeypatch.setattr(di_labels, "OUT_DIR", tmp_path / "di_training")
    n, warns = di_labels.generate([case["claim_id"]])
    assert n == 1 and warns == []
    labels = json.loads((tmp_path / "di_training" / f"{case['claim_id']}.pdf.labels.json").read_text(encoding="utf-8"))
    assert {lb["label"] for lb in labels["labels"]} == set(di_labels.FIELDS)
    assert (tmp_path / "di_training" / "fields.json").is_file()


def _label_boxes(labels: dict[str, object]) -> dict[str, list[float]]:
    return {lb["label"]: lb["value"][0]["boundingBoxes"][0] for lb in labels["labels"]}  # type: ignore[index,call-overload]


def test_di_labels_same_value_fields_get_distinct_boxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """出院日=開立日(72 案中 59 案)時兩欄位必須指向不同框,且開立日在頁面較下方。

    回歸:原本兩欄位都取第一個命中,DI Studio 以 Duplicated labels 拒絕(2026-09-30)。
    """
    case = next(
        c for c in all_cases()
        if c["documents"]["diagnosis_certificate"]["admissions"][0]["discharge_date"]
        == c["documents"]["diagnosis_certificate"]["issue_date"]
    )
    claims = tmp_path / "claims"
    generate(claims)
    render_case(claims / case["claim_id"])
    monkeypatch.setattr(di_labels, "CLAIMS_DIR", claims)
    monkeypatch.setattr(di_labels, "OUT_DIR", tmp_path / "di_training")
    n, warns = di_labels.generate([case["claim_id"]])
    assert n == 1 and warns == []
    boxes = _label_boxes(json.loads(
        (tmp_path / "di_training" / f"{case['claim_id']}.pdf.labels.json").read_text(encoding="utf-8")
    ))
    all_boxes = [tuple(b) for b in boxes.values()]
    assert len(all_boxes) == len(set(all_boxes)), "同一文件內不得有兩個欄位共用同一框"
    assert boxes["issue_date"][1] > boxes["discharge_date"][1], "開立日期在頁面下方"


def test_render_case_writes_incident_statement_when_present(tmp_path: Path):
    """第四類文件存在時多渲染一張 PDF,且文字層含事故經過;缺席時只有三張。"""
    with_inc = next(c for c in all_cases() if c["documents"]["incident_statement"] is not None)
    case_dir = tmp_path / with_inc["claim_id"]
    _write_claim(case_dir, with_inc)
    written = render_case(case_dir)
    assert [p.name for p in written][-1] == "incident_statement.pdf" and len(written) == 4
    with pdfplumber.open(written[-1]) as pdf:
        text = pdf.pages[0].extract_text()
    assert with_inc["documents"]["incident_statement"]["incident_date"] in text


def test_di_labels_wrapped_summary_spans_all_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """跨行的治療摘要必須標到每一行,拼接後等於原值(去空白)。

    回歸:原本整串搜不到就標點切段,長摘要只標第一句,DI 模型學到只抽第一句(2026-09-30 C008)。
    """
    case = next(c for c in all_cases() if c["claim_id"] == "C008")
    claims = tmp_path / "claims"
    generate(claims)
    render_case(claims / "C008")
    monkeypatch.setattr(di_labels, "CLAIMS_DIR", claims)
    monkeypatch.setattr(di_labels, "OUT_DIR", tmp_path / "di_training")
    n, warns = di_labels.generate(["C008"])
    assert n == 1 and warns == []
    labels = json.loads((tmp_path / "di_training" / "C008.pdf.labels.json").read_text(encoding="utf-8"))
    summary = next(lb for lb in labels["labels"] if lb["label"] == "treatment_summary")
    expected = case["documents"]["diagnosis_certificate"]["treatment_summary"].replace(" ", "")
    assert len(summary["value"]) >= 2, "C008 摘要在 PDF 內跨行,應有多個框"
    assert "".join(v["text"] for v in summary["value"]) == expected
    tops = [v["boundingBoxes"][0][1] for v in summary["value"]]
    assert tops == sorted(tops), "框依行由上而下"
