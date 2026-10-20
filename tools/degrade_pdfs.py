"""SPEC-ak 掃描化 / 雜訊 PDF 變體生成(魯棒性子集用;變體只落 check/ 與 Blob,不進 repo)。

PDF → pypdfium2 依等級 dpi 渲染 → 灰階 → 旋轉 → 模糊 → 對比 → 椒鹽雜點 → JPEG 再壓縮 → 包成**無文字層**的影像 PDF。
亂數種子 = sha256(<案號>/<文件名>|<等級>):同輸入位元組相同(DI 快取重跑命中),不同文件雜訊樣式不同。

用法(桌機):python -m tools.degrade_pdfs --level both --out check\\noisy C003 C010 …
輸出:<out>/<level>/<案號>/pdf/<原檔名>.pdf;只寫 pdf/(不複製真值);既有輸出未加 --force 拒絕覆寫(固定解法 11)。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import random
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DIR = ROOT / "data" / "claims"
# PIL 預設寫入當下時間 → 跨秒重跑位元組不同、DI 快取失效;固定為常數
FIXED_PDF_DATE = time.gmtime(1767225600)  # 2026-01-01T00:00:00Z;PdfParser 只接受 struct_time
FIXED_PDF_DATE_TAG = b"D:20260101000000Z"

# dpi / rotate(最大角度 ±) / blur(高斯半徑)/ contrast(<1 變淡)/ noise(椒鹽比例)/ jpeg_quality
LEVELS: dict[str, dict[str, float]] = {
    "mild": {"dpi": 200, "rotate": 1.0, "blur": 0.6, "contrast": 0.9, "noise": 0.01, "jpeg_quality": 75},
    "severe": {"dpi": 150, "rotate": 2.5, "blur": 1.2, "contrast": 0.7, "noise": 0.03, "jpeg_quality": 45},
}


def _rng(seed_key: str, level: str) -> random.Random:
    return random.Random(int(hashlib.sha256(f"{seed_key}|{level}".encode()).hexdigest()[:16], 16))


def _salt_pepper(img: Image.Image, ratio: float, rng: random.Random) -> Image.Image:
    """以種子亂數位元組當雜訊圖:低端 → 黑點、高端 → 白點(PIL 向量化,不逐像素迴圈)。"""
    noise = Image.frombytes("L", img.size, rng.randbytes(img.size[0] * img.size[1]))
    cut = int(ratio * 128)
    img = img.copy()
    img.paste(0, mask=noise.point(lambda v: 255 if v < cut else 0))
    img.paste(255, mask=noise.point(lambda v: 255 if v > 255 - cut else 0))
    return img


def degrade_pdf(pdf_bytes: bytes, level: str, seed_key: str) -> bytes:
    if level not in LEVELS:
        raise ValueError(f"未知等級 {level!r},可用:{sorted(LEVELS)}")
    p = LEVELS[level]
    rng = _rng(seed_key, level)
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        img = doc[0].render(scale=p["dpi"] / 72).to_pil().convert("L")
    finally:
        doc.close()
    img = img.rotate(rng.uniform(-p["rotate"], p["rotate"]), resample=Image.Resampling.BICUBIC,
                     expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(p["blur"]))
    img = ImageEnhance.Contrast(img).enhance(p["contrast"])
    img = _salt_pepper(img, p["noise"], rng)
    jpg = io.BytesIO()
    img.save(jpg, "JPEG", quality=int(p["jpeg_quality"]))
    out = io.BytesIO()
    Image.open(io.BytesIO(jpg.getvalue())).save(out, "PDF", resolution=p["dpi"],
                                              creationDate=FIXED_PDF_DATE, modDate=FIXED_PDF_DATE)
    return out.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SPEC-ak 掃描化 / 雜訊 PDF 變體")
    ap.add_argument("case_ids", nargs="+", help="案號(必填,不支援全量)")
    ap.add_argument("--level", choices=[*LEVELS, "both"], required=True)
    ap.add_argument("--src", default=str(CLAIMS_DIR))
    ap.add_argument("--out", required=True, help="輸出根目錄(建議 check/noisy,不入版控)")
    ap.add_argument("--force", action="store_true", help="覆寫既有輸出")
    args = ap.parse_args(argv)
    levels = list(LEVELS) if args.level == "both" else [args.level]
    src, out = Path(args.src), Path(args.out)
    jobs: list[tuple[Path, Path, str, str]] = []
    for cid in args.case_ids:
        pdfs = sorted((src / cid / "pdf").glob("*.pdf"))
        if not pdfs:
            print(f"略過 {cid}:{src / cid / 'pdf'} 無 PDF", file=sys.stderr)
            continue
        for level in levels:
            jobs += [(p, out / level / cid / "pdf" / p.name, level, f"{cid}/{p.stem}") for p in pdfs]
    existing = [dst for _, dst, _, _ in jobs if dst.exists()]
    if existing and not args.force:
        print(f"拒絕覆寫 {len(existing)} 個既有檔(首個 {existing[0]});確認後加 --force", file=sys.stderr)
        return 1
    for p, dst, level, key in jobs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(degrade_pdf(p.read_bytes(), level, key))
    print(f"完成 {len(jobs)} 檔 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
