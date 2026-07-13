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

Device image:  ~/tools/rm2_device.jpg  (CC BY-SA, Wikimedia Commons)
Screen region: (185, 214) → (1779, 2477)  measured from the raw 1860×2556 photo

Dependencies: Pillow, pdftoppm (poppler-utils)
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

DEVICE_IMAGE = Path(__file__).parent / "rm2_device.jpg"

# Screen region in the 1860×2556 base image (left, top, right, bottom)
SCREEN_LEFT   = 185
SCREEN_TOP    = 214
SCREEN_RIGHT  = 1779
SCREEN_BOTTOM = 2477

# E-ink background colour used for letterboxing
EINK_BG = (215, 218, 220)


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


def _composite(device: Image.Image, page: Image.Image) -> Image.Image:
    sw = SCREEN_RIGHT  - SCREEN_LEFT
    sh = SCREEN_BOTTOM - SCREEN_TOP

    # Fit page inside screen, preserving aspect ratio; pad with e-ink background
    pw, ph = page.size
    scale = min(sw / pw, sh / ph)
    nw, nh = int(pw * scale), int(ph * scale)

    page_rs = page.resize((nw, nh), Image.LANCZOS)

    canvas = Image.new("RGB", (sw, sh), EINK_BG)
    ox = (sw - nw) // 2
    oy = (sh - nh) // 2
    canvas.paste(page_rs, (ox, oy))

    result = device.copy()
    result.paste(canvas, (SCREEN_LEFT, SCREEN_TOP))
    return result


def _page_count(pdf: Path) -> int:
    out = subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True
    )
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[-1])
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="Composite PDF/PNG into a reMarkable 2 device photo"
    )
    ap.add_argument("input", help="PDF or PNG file")
    ap.add_argument("--page", type=int, default=1, metavar="N",
                    help="Page number for PDF input (default: 1)")
    ap.add_argument("--out", help="Output path (default: <stem>_rm2.png)")
    ap.add_argument("--dpi", type=int, default=226,
                    help="Render DPI for PDF (default: 226)")
    ap.add_argument("--scale", type=float, default=0.5,
                    help="Output scale factor (default: 0.5)")
    ap.add_argument("--all-pages", action="store_true",
                    help="Export all PDF pages as separate images")
    ap.add_argument("--no-grayscale", action="store_true",
                    help="Keep colour (default: grayscale to simulate e-ink)")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    if not DEVICE_IMAGE.exists():
        print(f"Error: device image not found at {DEVICE_IMAGE}", file=sys.stderr)
        sys.exit(1)

    device = Image.open(DEVICE_IMAGE).convert("RGB")

    is_pdf = input_path.suffix.lower() == ".pdf"
    pages = range(1, _page_count(input_path) + 1) if (args.all_pages and is_pdf) \
            else [args.page]

    for page_num in pages:
        print(f"  [page {page_num}] rendering at {args.dpi} DPI...", end=" ", flush=True)
        page_img = _load_page(input_path, page_num, args.dpi)

        if not args.no_grayscale:
            page_img = page_img.convert("L").convert("RGB")

        result = _composite(device, page_img)

        if args.scale != 1.0:
            nw = int(result.width  * args.scale)
            nh = int(result.height * args.scale)
            result = result.resize((nw, nh), Image.LANCZOS)

        if args.out and not args.all_pages:
            out_path = Path(args.out)
        elif args.all_pages and is_pdf:
            total = max(pages)
            digits = len(str(total))
            out_path = input_path.with_name(
                f"{input_path.stem}_rm2_p{str(page_num).zfill(digits)}.png"
            )
        else:
            out_path = input_path.with_name(f"{input_path.stem}_rm2.png")

        result.save(out_path, optimize=True)
        print(f"→ {out_path}  ({result.width}×{result.height})")


if __name__ == "__main__":
    main()
