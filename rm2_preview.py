#!/usr/bin/env python3
"""rm2_preview.py - Live reMarkable 2 preview for reCompose pipeline output.

Serves http://localhost:7700

Auto-watches the pipeline output directory (default
~/vault/dev/projects/rm2-pipeline/) and shows the latest built PDF, page by
page, rendered inside a clean CSS reMarkable 2 device frame (real bezel,
rounded corners, e-ink grayscale). No upload UI - it just shows whatever was
most recently built.

WHY A CSS FRAME INSTEAD OF THE DEVICE PHOTO?
  The CC-licensed RM2 photo on Wikimedia is a "screen-on" flat-lay where the
  display is a transparent window onto the grey surface behind the tablet.
  Pasting a report page into that photo leaves a grey ring (the surface
  showing through) and the corners never line up. A drawn frame gives
  pixel-accurate alignment and a properly visible bezel. For shareable stills
  composited into the real device photo, use rm2_mockup.py.

Usage:
  python3 rm2_preview.py [--port 7700] [--dir /path/to/pdf/output]
"""

import argparse
import io
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from PIL import Image

DEFAULT_DIR = Path.home() / "vault" / "dev" / "projects" / "rm2-pipeline"

app = FastAPI()

# Live state, refreshed on each /api/state call.
state = {"path": None, "mtime": 0.0, "pages": 0, "name": "(no PDF found)"}

# The RM2 canvas is 157.8 x 210.4 mm -> aspect 210.4/157.8 = 1.3333.
# Screen is drawn at 360 x 480 px to match that ratio exactly (no distortion).
SCREEN_W, SCREEN_H = 360, 480


def _find_latest(watch_dir: Path) -> Path | None:
    pdfs = list(watch_dir.glob("*.pdf"))
    if not pdfs:
        return None
    return max(pdfs, key=lambda f: f.stat().st_mtime)


def _refresh(watch_dir: Path) -> None:
    p = _find_latest(watch_dir)
    if p is None:
        state.update(path=None, mtime=0.0, pages=0, name="(no PDF found)")
        return
    mtime = p.stat().st_mtime
    if state["path"] != str(p) or state["mtime"] != mtime:
        state.update(path=str(p), mtime=mtime,
                     pages=_page_count(p), name=p.name)


def _page_count(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[-1])
    return 1


def _render_page(pdf: Path, page: int, dpi: int = 226) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf.read_bytes())
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png",
             "-f", str(page), "-l", str(page), str(src), f"{tmp}/p"],
            check=True, capture_output=True,
        )
        pngs = sorted(Path(tmp).glob("*.png"))
        if not pngs:
            raise RuntimeError("pdftoppm produced no output")
        im = Image.open(pngs[0]).convert("L")  # e-ink grayscale
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


HTML = """\
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>reCompose - RM2 Live Preview</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #232427; color: #cfcfcf;
         font-family: system-ui, -apple-system, sans-serif;
         min-height: 100vh; display: flex; flex-direction: column; }
  header { background: #15161a; padding: 12px 20px;
           display: flex; align-items: center; gap: 14px;
           border-bottom: 1px solid #2c2d33; }
  header h1 { font-size: .95rem; font-weight: 600; letter-spacing: .04em; color: #fff; }
  header .file { font-size: .78rem; color: #7d7f86; margin-left: auto; }
  .stage { flex: 1; display: flex; flex-direction: column;
           align-items: center; justify-content: center;
           gap: 20px; padding: 30px 16px; }

  /* reMarkable 2 device frame, drawn in CSS.
     Light warm-grey body, rounded corners (the bezel), thin metallic edge. */
  .device { position: relative; background: #e7e7e3; border-radius: 22px;
            padding: 22px 26px 46px 26px; border: 1px solid #cbcbc6;
            box-shadow: 0 18px 50px rgba(0,0,0,.55), inset 0 1px 0 #fff; }
  /* Marker (pen) magnetically attached to the right long edge. */
  .device .marker { position: absolute; right: -10px; top: 96px;
            width: 10px; height: 236px;
            background: linear-gradient(90deg, #9a9a95, #efefe9 45%, #b6b6b0);
            border-radius: 0 6px 6px 0; box-shadow: 1px 0 2px rgba(0,0,0,.2); }
  .screen { position: relative; width: 360px; height: 480px;
            background: #f6f6f3; overflow: hidden;
            box-shadow: inset 0 0 0 1px #d4d4cf; }
  .screen img { display: block; width: 100%; height: 100%; object-fit: contain; }

  .controls { display: flex; align-items: center; gap: 18px; }
  .btn { background: #33343a; border: 1px solid #4a4b52; color: #e3e3e3;
         padding: 9px 20px; border-radius: 7px; cursor: pointer;
         font-size: .85rem; transition: background .15s; }
  .btn:hover:not(:disabled) { background: #41424a; }
  .btn:disabled { opacity: .3; cursor: default; }
  .page-info { font-size: .85rem; color: #a9abb1; min-width: 96px; text-align: center; }
  .hint { font-size: .72rem; color: #5f6168; }
</style>
</head>
<body>
<header>
  <h1>reCompose &middot; RM2 Live Preview</h1>
  <span class="file" id="fname"></span>
</header>

<div class="stage">
  <div class="device">
    <div class="marker"></div>
    <div class="screen"><img id="pg" src="" alt="page"></div>
  </div>
  <div class="controls">
    <button class="btn" id="prev" onclick="go(-1)" disabled>Prev</button>
    <div class="page-info" id="info">&mdash;</div>
    <button class="btn" id="next" onclick="go(1)" disabled>Next</button>
  </div>
  <div class="hint">Auto-watching pipeline output &middot; arrow keys to navigate</div>
</div>

<script>
let page = 1, pages = 0, ts = 0;
const img   = document.getElementById('pg');
const info  = document.getElementById('info');
const prev  = document.getElementById('prev');
const next  = document.getElementById('next');
const fname = document.getElementById('fname');

async function poll() {
  const r = await fetch('/api/state');
  const s = await r.json();
  if (s.name !== fname.textContent || s.ts !== ts) {
    fname.textContent = s.name;
    ts = s.ts; pages = s.pages;
    if (page > pages) page = 1;
    load();
  }
}

function load() {
  if (!pages) {
    img.removeAttribute('src');
    info.textContent = 'no pdf';
    prev.disabled = next.disabled = true;
    return;
  }
  img.style.opacity = .45;
  img.src = '/page?n=' + page + '&t=' + ts;
  img.onload = () => {
    img.style.opacity = 1;
    info.textContent = 'Page ' + page + ' / ' + pages;
    prev.disabled = page <= 1;
    next.disabled = page >= pages;
  };
}

function go(d) {
  if (page + d >= 1 && page + d <= pages) { page += d; load(); }
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') go(1);
  if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   go(-1);
});

setInterval(poll, 2000);
poll();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.get("/api/state")
async def api_state():
    _refresh(APP_WATCH_DIR)
    return {"name": state["name"], "pages": state["pages"], "ts": state["mtime"]}


@app.get("/page")
async def page(n: int = 1):
    _refresh(APP_WATCH_DIR)
    if not state["path"]:
        raise HTTPException(404, "No PDF in watch directory")
    pdf = Path(state["path"])
    n = max(1, min(n, state["pages"]))
    try:
        png = _render_page(pdf, n)
    except Exception as e:
        raise HTTPException(500, f"render failed: {e}")
    return Response(content=png, media_type="image/png")


APP_WATCH_DIR = DEFAULT_DIR


def main():
    global APP_WATCH_DIR
    ap = argparse.ArgumentParser(description="reCompose RM2 live preview")
    ap.add_argument("--port", type=int, default=7700)
    ap.add_argument("--dir", type=str, default=str(DEFAULT_DIR),
                    help="Directory to watch for the latest *.pdf")
    args = ap.parse_args()
    APP_WATCH_DIR = Path(args.dir).expanduser()
    print(f"  reCompose RM2 preview -> http://localhost:{args.port}")
    print(f"  watching: {APP_WATCH_DIR}")
    uvicorn_run(app, args.port)


# Lazy import uvicorn so --help works without it installed.
def uvicorn_run(app, port):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
