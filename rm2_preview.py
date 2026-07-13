#!/usr/bin/env python3
"""rm2_preview.py — Browser-based preview for the reCompose mockup pipeline.

Serves on http://localhost:7700

Usage:
  python3 rm2_preview.py [--port 7700] [--dir /path/to/pdfs]

Drop PDFs into the working directory (default: same dir as this script),
then open http://localhost:7700 to browse pages as RM2 mockups.
"""

import argparse
import io
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, Response
from PIL import Image, ImageDraw

# ── mockup logic (inlined from rm2_mockup.py) ────────────────────────────────

HERE         = Path(__file__).resolve().parent
DEVICE_IMAGE = HERE / "rm2_device.jpg"

# Screen corners measured by Sobel edge detection on the 1860×2556 device photo.
# Top is 15px wider than bottom (camera keystone) — perspective warp corrects it.
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
    A, b = [], []
    for (xs, ys), (xd, yd) in zip(src_pts, dst_pts):
        A.append([xd, yd, 1, 0,  0,  0, -xs*xd, -xs*yd])
        A.append([0,  0,  0, xd, yd, 1, -ys*xd, -ys*yd])
        b.extend([xs, ys])
    coeffs, _, _, _ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
    return tuple(float(c) for c in coeffs)


def _pdf_page(pdf_bytes: bytes, page: int, dpi: int = 226) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.pdf"
        src.write_bytes(pdf_bytes)
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png",
             "-f", str(page), "-l", str(page),
             str(src), f"{tmp}/p"],
            check=True, capture_output=True,
        )
        pages = sorted(Path(tmp).glob("*.png"))
        if not pages:
            raise RuntimeError(f"pdftoppm: no output for page {page}")
        return Image.open(pages[0]).copy()


def _page_count(pdf_bytes: bytes) -> int:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        fname = f.name
    out = subprocess.run(["pdfinfo", fname], capture_output=True, text=True)
    Path(fname).unlink(missing_ok=True)
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[-1])
    return 1


def _composite(page_img: Image.Image, grayscale: bool = True) -> bytes:
    if grayscale:
        page_img = page_img.convert("L").convert("RGB")

    TL = SCREEN_CORNERS["TL"]
    TR = SCREEN_CORNERS["TR"]
    BL = SCREEN_CORNERS["BL"]
    BR = SCREEN_CORNERS["BR"]
    PW, PH = page_img.size
    device  = _device()
    DW, DH  = device.size

    coeffs = _perspective_coeffs(
        src_pts=[(0, 0), (PW, 0), (0, PH), (PW, PH)],
        dst_pts=[TL,     TR,      BL,      BR     ],
    )
    warped = page_img.transform((DW, DH), Image.PERSPECTIVE, coeffs, Image.BICUBIC)

    mask = Image.new("L", (DW, DH), 0)
    ImageDraw.Draw(mask).polygon([TL, TR, BR, BL], fill=255)

    result = device.copy()
    result.paste(warped, mask=mask)

    result = result.resize((result.width // 2, result.height // 2), Image.LANCZOS)
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ── session state ─────────────────────────────────────────────────────────────

class Session:
    pdf_bytes: bytes | None = None
    page_count: int = 0
    filename: str = ""

session = Session()

# ── app ───────────────────────────────────────────────────────────────────────

app = FastAPI()

HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>reCompose Preview</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #1a1a1a; color: #ddd; font-family: system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
  header { width: 100%; background: #111; padding: 14px 24px;
           display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #333; }
  header h1 { font-size: 1rem; font-weight: 600; color: #fff; letter-spacing: .05em; }
  header span { font-size: .8rem; color: #888; }
  .upload-zone { margin: 40px auto; padding: 32px 48px; border: 2px dashed #444;
                 border-radius: 12px; text-align: center; cursor: pointer; max-width: 480px;
                 transition: border-color .2s; }
  .upload-zone:hover { border-color: #888; }
  .upload-zone input { display: none; }
  .upload-zone label { cursor: pointer; color: #aaa; font-size: .95rem; }
  .upload-zone label strong { color: #ddd; }
  .viewer { display: flex; flex-direction: column; align-items: center; gap: 20px;
            padding: 32px 16px; width: 100%; }
  .device-frame { border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,.6); max-width: 480px; width: 100%; }
  .controls { display: flex; align-items: center; gap: 20px; }
  .btn { background: #333; border: 1px solid #555; color: #ddd; padding: 8px 20px;
         border-radius: 6px; cursor: pointer; font-size: .9rem; transition: background .15s; }
  .btn:hover:not(:disabled) { background: #444; }
  .btn:disabled { opacity: .35; cursor: default; }
  .page-info { font-size: .9rem; color: #aaa; min-width: 80px; text-align: center; }
  .filename { font-size: .8rem; color: #666; margin-top: -8px; }
  #spinner { display: none; color: #888; font-size: .85rem; }
  .drop-active { border-color: #aaa !important; background: #222; }
</style>
</head>
<body>

<header>
  <h1>reCompose Preview</h1>
  <span id="header-file"></span>
</header>

<div id="upload-section">
  <div class="upload-zone" id="drop-zone">
    <input type="file" id="file-input" accept=".pdf">
    <label for="file-input">
      <strong>Choose a PDF</strong> or drag it here
    </label>
  </div>
</div>

<div class="viewer" id="viewer" style="display:none">
  <div class="filename" id="filename-label"></div>
  <img class="device-frame" id="mockup-img" src="" alt="RM2 mockup">
  <div id="spinner">Rendering…</div>
  <div class="controls">
    <button class="btn" id="btn-prev" onclick="changePage(-1)" disabled>← Prev</button>
    <div class="page-info" id="page-info">Page 1 / 1</div>
    <button class="btn" id="btn-next" onclick="changePage(1)" disabled>Next →</button>
  </div>
</div>

<script>
let currentPage = 1;
let totalPages  = 1;

const dropZone  = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const viewer    = document.getElementById('viewer');
const upload    = document.getElementById('upload-section');
const img       = document.getElementById('mockup-img');
const spinner   = document.getElementById('spinner');
const pageInfo  = document.getElementById('page-info');
const btnPrev   = document.getElementById('btn-prev');
const btnNext   = document.getElementById('btn-next');

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drop-active'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drop-active'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drop-active');
  const f = e.dataTransfer.files[0];
  if (f && f.name.endsWith('.pdf')) uploadFile(f);
});
fileInput.addEventListener('change', e => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  spinner.style.display = 'block';
  const res = await fetch('/upload', { method: 'POST', body: fd });
  const data = await res.json();
  totalPages  = data.pages;
  currentPage = 1;
  document.getElementById('filename-label').textContent = file.name;
  document.getElementById('header-file').textContent = file.name;
  upload.style.display  = 'none';
  viewer.style.display  = 'flex';
  await loadPage(1);
}

async function loadPage(n) {
  spinner.style.display = 'block';
  img.style.opacity = '0.4';
  img.src = `/mockup?page=${n}&t=${Date.now()}`;
  img.onload = () => {
    img.style.opacity = '1';
    spinner.style.display = 'none';
    pageInfo.textContent = `Page ${n} / ${totalPages}`;
    btnPrev.disabled = (n <= 1);
    btnNext.disabled = (n >= totalPages);
    currentPage = n;
  };
}

function changePage(delta) {
  const next = currentPage + delta;
  if (next >= 1 && next <= totalPages) loadPage(next);
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') changePage(1);
  if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   changePage(-1);
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    session.pdf_bytes  = data
    session.filename   = file.filename or "document.pdf"
    session.page_count = _page_count(data)
    return {"pages": session.page_count, "filename": session.filename}


@app.get("/mockup")
async def mockup(page: int = 1):
    if not session.pdf_bytes:
        raise HTTPException(404, "No PDF uploaded")
    page = max(1, min(page, session.page_count))
    page_img = _pdf_page(session.pdf_bytes, page)
    jpg = _composite(page_img, grayscale=True)
    return Response(content=jpg, media_type="image/jpeg")


def main():
    ap = argparse.ArgumentParser(description="reCompose browser preview")
    ap.add_argument("--port", type=int, default=7700)
    args = ap.parse_args()
    print(f"  reCompose preview → http://localhost:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
