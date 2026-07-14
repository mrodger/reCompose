#!/usr/bin/env python3
"""rm2_mockup.py — Composite a PDF page or PNG into a reMarkable 2 device photo.

Usage:
  python3 rm2_mockup.py document.pdf [options]
  python3 rm2_mockup.py page.png [options]

Options:
  --page N        PDF page to render (1-indexed, default: 1)
  --out PATH      Output PNG path (default: <stem>_rm2.png beside input)
  --dpi N         Render DPI for PDF (default: 226 = native RM2 DPI)
  --scale F       Scale the final output (default: 0.5, i.e. half-size for sharing)
  --all-pages     Export every page; output files are <stem>_rm2_p01.png etc.
  --no-grayscale  Keep colour (default: convert to grayscale to simulate e-ink)

Device image:  rm2_device.jpg (alongside this script, CC BY-SA 4.0, Wikimedia Commons)
Screen corners measured via Sobel edge detection on the 1860×2556 photo:
  TL=(183,257)  TR=(1775,253)  BL=(194,2475)  BR=(1771,2470)
The screen has a 15px keystone (top 1592px wide, bottom 1577px wide) from
camera angle — the perspective warp corrects this exactly.

Dependencies: Pillow, numpy, pdftoppm (poppler-utils)
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

DEVICE_IMAGE = Path(__file__).resolve().parent / "rm2_device.jpg"

# Screen corners in the 1860×2556 device photo, measured by Sobel edge peak.
# Order: top-left, top-right, bottom-left, bottom-right
SCREEN_CORNERS = {
    "TL": (183, 257),
    "TR": (1775, 253),
    "BL": (194, 2475),
    "BR": (1771, 2470),
}

_device_cache: Image.Image | None = None


def _device() -> Image.Image:
    global _device_cache
    if _device_cache is None:
        _device_cache = Image.open(DEVICE_IMAGE).convert("RGB")
    return _device_cache


def _perspective_coeffs(src_pts, dst_pts):
    """Compute 8 PIL PERSPECTIVE coefficients mapping dst→src.

    PIL's transform samples the *source* image for each *destination* pixel:
        x_src = (a*xd + b*yd + c) / (g*xd + h*yd + 1)
        y_src = (d*xd + e*yd + f) / (g*xd + h*yd + 1)

    src_pts: 4 (x,y) points in the source (page) image
    dst_pts: 4 corresponding (x,y) points in the destination (device photo)
    """
    A, b = [], []
    for (xs, ys), (xd, yd) in zip(src_pts, dst_pts):
        A.append([xd, yd, 1, 0,  0,  0, -xs*xd, -xs*yd])
        A.append([0,  0,  0, xd, yd, 1, -ys*xd, -ys*yd])
        b.extend([xs, ys])
    coeffs, _, _, _ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
    return tuple(float(c) for c in coeffs)


def _composite(device: Image.Image, page: Image.Image) -> Image.Image:
    TL = SCREEN_CORNERS["TL"]
    TR = SCREEN_CORNERS["TR"]
    BL = SCREEN_CORNERS["BL"]
    BR = SCREEN_CORNERS["BR"]
    PW, PH = page.size
    DW, DH = device.size

    # Map page corners → screen corners (PIL transform goes dst→src, so invert)
    coeffs = _perspective_coeffs(
        src_pts=[(0, 0), (PW, 0), (0, PH), (PW, PH)],
        dst_pts=[TL,     TR,      BL,      BR     ],
    )

    warped = page.transform(
        (DW, DH), Image.PERSPECTIVE, coeffs, Image.BICUBIC
    )

    # Mask: only paste within the screen quadrilateral
    mask = Image.new("L", (DW, DH), 0)
    ImageDraw.Draw(mask).polygon([TL, TR, BR, BL], fill=255)

    result = device.copy()
    result.paste(warped, mask=mask)
    return result


def _pdf_page_to_pil(pdf: Path, page: int, dpi: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png",
             "-f", str(page), "-l", str(page),
             str(pdf), f"{tmp}/p"],
            check=True, capture_output=True,
        )
        pages = sorted(Path(tmp).glob("*.png"))
        if not pages:
            raise RuntimeError(f"pdftoppm: no output for page {page} of {pdf}")
        return Image.open(pages[0]).copy()


def _load_page(path: Path, page: int, dpi: int) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        return _pdf_page_to_pil(path, page, dpi)
    return Image.open(path).convert("RGB")


def _page_count(pdf: Path, dpi: int = 226) -> int:
    """Count pages with pdftoppm (already required for compositing), probing
    one page at a time until rendering fails. This keeps a single page-count
    source of truth with rm2_preview.py — both derive the count from pdftoppm
    output, so no separate pdfinfo dependency is needed.
    """
    n = 0
    with tempfile.TemporaryDirectory() as tmp:
        while True:
            n += 1
            r = subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-png",
                 "-f", str(n), "-l", str(n), str(pdf), f"{tmp}/p"],
                capture_output=True,
            )
            if r.returncode != 0 or not list(Path(tmp).glob(f"p-{n}.png")):
                n -= 1
                break
    return max(n, 1)


def main():
    ap = argparse.ArgumentParser(
        description="Composite PDF/PNG into a reMarkable 2 device photo"
    )
    ap.add_argument("input", help="PDF or PNG file")
    ap.add_argument("--page", type=int, default=1, metavar="N")
    ap.add_argument("--out", help="Output path (default: <stem>_rm2.png)")
    ap.add_argument("--dpi", type=int, default=226)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--all-pages", action="store_true")
    ap.add_argument("--no-grayscale", action="store_true")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    is_pdf = input_path.suffix.lower() == ".pdf"
    pages  = range(1, _page_count(input_path) + 1) if (args.all_pages and is_pdf) \
             else [args.page]

    for page_num in pages:
        print(f"  [page {page_num}] rendering...", end=" ", flush=True)
        page_img = _load_page(input_path, page_num, args.dpi)
        if not args.no_grayscale:
            page_img = page_img.convert("L").convert("RGB")

        result = _composite(_device(), page_img)

        if args.scale != 1.0:
            result = result.resize(
                (int(result.width * args.scale), int(result.height * args.scale)),
                Image.LANCZOS,
            )

        if args.out and not args.all_pages:
            out_path = Path(args.out)
        elif args.all_pages and is_pdf:
            digits   = len(str(max(pages)))
            out_path = input_path.with_name(
                f"{input_path.stem}_rm2_p{str(page_num).zfill(digits)}.png"
            )
        else:
            out_path = input_path.with_name(f"{input_path.stem}_rm2.png")

        result.save(out_path, optimize=True)
        print(f"→ {out_path}  ({result.width}×{result.height})")


if __name__ == "__main__":
    main()
