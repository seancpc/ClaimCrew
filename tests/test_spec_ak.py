"""SPEC-ak:掃描化 / 雜訊 PDF 變體生成(⑪ 魯棒性方案 A)。

真正要量的是「DI 讀錯時是否往人工走」,本檔只守住變體本身:純影像、確定性、等級可區分、符合 DI F0 限制、
批次防護(固定解法 11)、不帶真值。子集 = POL-C 核付 13 案(收據金額為主),13 × 3 文件 × 2 等級 = 78 頁。
"""
from __future__ import annotations

import io
from pathlib import Path

import pdfplumber
import pytest

from tools import degrade_pdfs as dg

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "data" / "claims"
SUBSET = ["C003", "C010", "C032", "C033", "C034", "C035", "C036", "C055", "C059", "C066", "C069", "C070", "C071"]
F0_MAX_BYTES = 4 * 1024 * 1024


def _src(cid: str = "C003", doc: str = "medical_receipt") -> bytes:
    return (CLAIMS / cid / "pdf" / f"{doc}.pdf").read_bytes()


def _pages_and_text(pdf: bytes) -> tuple[int, str]:
    with pdfplumber.open(io.BytesIO(pdf)) as d:
        return len(d.pages), "".join((p.extract_text() or "") for p in d.pages)


# ── 行為 1:純影像 PDF ─────────────────────────────
@pytest.mark.parametrize("level", ["mild", "severe"])
def test_ak1_output_is_single_page_without_text_layer(level):
    assert _pages_and_text(_src())[1].strip(), "前提:原檔有文字層"
    n, text = _pages_and_text(dg.degrade_pdf(_src(), level, "C003/medical_receipt"))
    assert n == 1 and text.strip() == ""


# ── 行為 2:確定性 ─────────────────────────────
@pytest.mark.parametrize("level", ["mild", "severe"])
def test_ak2_deterministic_bytes(level):
    a = dg.degrade_pdf(_src(), level, "C003/medical_receipt")
    b = dg.degrade_pdf(_src(), level, "C003/medical_receipt")
    assert a == b


def test_ak2_seed_depends_on_key():
    """同等級、不同文件鍵 → 劣化亂數不同(避免全部文件同一雜訊樣式)。"""
    src = _src()
    assert dg.degrade_pdf(src, "severe", "C003/medical_receipt") != dg.degrade_pdf(src, "severe", "C999/x")


# ── 行為 3:等級區隔 ─────────────────────────────
def test_ak3_levels_differ_from_each_other_and_from_source():
    src = _src()
    mild, severe = dg.degrade_pdf(src, "mild", "k"), dg.degrade_pdf(src, "severe", "k")
    assert len({src, mild, severe}) == 3


def test_ak3_severe_has_lower_resolution_and_stronger_noise_params():
    m, s = dg.LEVELS["mild"], dg.LEVELS["severe"]
    assert s["dpi"] < m["dpi"] and s["jpeg_quality"] < m["jpeg_quality"]
    assert s["noise"] > m["noise"] and s["blur"] > m["blur"] and s["rotate"] > m["rotate"]


def test_ak3_unknown_level_rejected():
    with pytest.raises(ValueError):
        dg.degrade_pdf(_src(), "extreme", "k")


# ── 行為 4:DI F0 限制(全子集 × 兩等級)────────────────
def test_ak4_full_subset_within_f0_limits():
    docs = [p for cid in SUBSET for p in sorted((CLAIMS / cid / "pdf").glob("*.pdf"))]
    assert len(docs) == 39
    for p in docs:
        for level in ("mild", "severe"):
            out = dg.degrade_pdf(p.read_bytes(), level, f"{p.parent.parent.name}/{p.stem}")
            assert len(out) < F0_MAX_BYTES and _pages_and_text(out)[0] == 1, (p, level)


# ── 行為 5 / 6:CLI 批次防護、不帶真值 ─────────────────
def _run(tmp: Path, *args: str) -> int:
    return dg.main(["--src", str(CLAIMS), "--out", str(tmp), *args])


def test_ak5_only_requested_cases_and_only_pdf_dir(tmp_path):
    assert _run(tmp_path, "--level", "mild", "C003") == 0
    assert sorted(p.name for p in (tmp_path / "mild").iterdir()) == ["C003"]
    case = tmp_path / "mild" / "C003"
    assert sorted(p.name for p in case.iterdir()) == ["pdf"]  # 行為 6:不複製 truth.json / claim.json
    assert sorted(p.name for p in (case / "pdf").iterdir()) == sorted(
        p.name for p in (CLAIMS / "C003" / "pdf").glob("*.pdf"))
    assert not (tmp_path / "severe").exists()


def test_ak5_both_levels(tmp_path):
    assert _run(tmp_path, "--level", "both", "C003") == 0
    assert (tmp_path / "mild" / "C003" / "pdf").is_dir() and (tmp_path / "severe" / "C003" / "pdf").is_dir()


def test_ak5_missing_case_skipped_not_created(tmp_path, capsys):
    assert _run(tmp_path, "--level", "mild", "C003", "C999") == 0
    assert not (tmp_path / "mild" / "C999").exists()
    assert "C999" in capsys.readouterr().err


def test_ak5_existing_output_refused_without_force(tmp_path):
    assert _run(tmp_path, "--level", "mild", "C003") == 0
    target = tmp_path / "mild" / "C003" / "pdf" / "medical_receipt.pdf"
    target.write_bytes(b"sentinel")
    assert _run(tmp_path, "--level", "mild", "C003") == 1
    assert target.read_bytes() == b"sentinel"
    assert _run(tmp_path, "--level", "mild", "C003", "--force") == 0
    assert target.read_bytes() == dg.degrade_pdf(_src(), "mild", "C003/medical_receipt")


def test_ak5_requires_case_ids(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, "--level", "mild")


def test_ak6_cli_output_matches_function_seed_key(tmp_path):
    """CLI 的亂數鍵 = <案號>/<文件名>;與函式直呼一致,確保桌機重生成位元組相同、DI 快取命中。"""
    assert _run(tmp_path, "--level", "severe", "C069") == 0
    for p in sorted((CLAIMS / "C069" / "pdf").glob("*.pdf")):
        assert (tmp_path / "severe" / "C069" / "pdf" / p.name).read_bytes() == dg.degrade_pdf(
            p.read_bytes(), "severe", f"C069/{p.stem}")


def test_ak2_no_wall_clock_timestamp():
    """PIL 預設把當下時間寫入 /CreationDate、/ModDate → 跨秒重跑位元組不同、DI 快取失效。
    ak2(同秒兩次呼叫)抓不到,故直接斷言日期為固定值。"""
    out = dg.degrade_pdf(_src(), "mild", "k")
    assert dg.FIXED_PDF_DATE_TAG in out
    assert out.count(b"/CreationDate") == 1 and out.count(b"/ModDate") == 1


def _margin_dark_ratio(level: str) -> float:
    """以變體自身 dpi 渲染(縮到 72dpi 會把孤立雜點平均掉),量頁面下方空白區的黑點比例。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(dg.degrade_pdf(_src(), level, "k"))
    img = doc[0].render(scale=dg.LEVELS[level]["dpi"] / 72).to_pil().convert("L")
    doc.close()
    w, h = img.size
    region = img.crop((w // 4, int(h * 0.75), w * 3 // 4, int(h * 0.95)))
    hist = region.histogram()
    return sum(hist[:128]) / (region.size[0] * region.size[1])


def test_ak3_salt_pepper_present_and_scales_with_level():
    """原檔頁面下方為空白;劣化後該區須有黑點(椒鹽),且重度多於輕度。"""
    mild, severe = _margin_dark_ratio("mild"), _margin_dark_ratio("severe")
    assert mild > dg.LEVELS["mild"]["noise"] * 0.1
    assert severe > mild
