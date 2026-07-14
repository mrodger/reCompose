#!/usr/bin/env python3
"""rm2_preview.py - Live reMarkable 2 preview for reCompose pipeline output.

Serves http://localhost:7700

Shows a chosen PDF (default: the latest built in the current directory;
override the watch location with --dir) page by page, rendered inside a clean
CSS reMarkable 2 device frame (real
bezel, rounded corners, e-ink grayscale). Every page is pre-rendered into a
memory cache and preloaded by the browser, so Prev/Next navigation is instant.

Pin a specific document with --file (path, or a substring/glob matched inside
the watch dir, latest match by mtime wins):

    python3 rm2_preview.py --file lyzr-platform-analysis-v2

WHY A CSS FRAME INSTEAD OF THE DEVICE PHOTO?
  The CC-licensed RM2 photo on Wikimedia is a "screen-on" flat-lay where the
  display is a transparent window onto the grey surface behind the tablet.
  Pasting a report page into that photo leaves a grey ring (the surface
  showing through) and the corners never line up. A drawn frame gives
  pixel-accurate alignment and a properly visible bezel. For shareable stills
  composited into the real device photo, use rm2_mockup.py.

Usage:
  python3 rm2_preview.py [--port 7700] [--dir /path/to/pdf/output] [--file NAME]
"""

import argparse
import io
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from PIL import Image

# Default watch directory: the current working directory, so `make` output
# (built in cwd) is previewed by simply running `python3 rm2_preview.py`.
# Override with --dir if your PDFs live elsewhere.
DEFAULT_DIR = Path.cwd()

app = FastAPI()

# Live state, refreshed on each /api/state call.
state = {"path": None, "mtime": 0.0, "pages": 0,
         "name": "(no PDF found)", "title": "(no document)"}

# The RM2 canvas is 157.8 x 210.4 mm -> aspect 210.4/157.8 = 1.3333.
# Screen is drawn at 360 x 480 px to match that ratio exactly (no distortion).
SCREEN_W, SCREEN_H = 360, 480

# In-memory cache of rendered page PNGs, keyed by (path, mtime, page#).
page_cache: dict = {}

APP_WATCH_DIR = DEFAULT_DIR
APP_FILE_ARG: str | None = None


def _resolve_file(watch_dir: Path, file_arg: str | None) -> Path | None:
    """Resolve which PDF to display.

    - No file_arg  -> latest *.pdf in watch_dir.
    - file_arg is an existing path -> that file.
    - otherwise    -> substring/glob match inside watch_dir, latest by mtime.
    """
    if file_arg is None:
        pdfs = list(watch_dir.glob("*.pdf"))
    elif Path(file_arg).exists():
        return Path(file_arg).expanduser()
    else:
        # Match only PDFs so sibling .md/.tex files can't be picked.
        pdfs = list(watch_dir.glob(f"*{file_arg}*.pdf"))
        if not pdfs:
            pdfs = sorted(watch_dir.glob("*.pdf"))
    if not pdfs:
        return None
    return max(pdfs, key=lambda f: f.stat().st_mtime)


def _title(pdf: Path) -> str:
    """Best-effort document title from page 1 text, else the filename stem."""
    try:
        out = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
            capture_output=True, text=True)
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if lines:
            return lines[0][:90]
    except Exception:
        pass
    return pdf.stem


def _prerender_all(pdf: Path, mtime: float, dpi: int = 226) -> int:
    """Render every page once into page_cache so navigation is instant.

    Returns the number of pages actually produced. We derive the page count
    from the rendered PNGs (not from pdfinfo, which has been unreliable here),
    so the count is always authoritative.
    """
    page_cache.clear()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf.read_bytes())
        r = subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png",
             "-f", "1", str(src), f"{tmp}/p"],
            capture_output=True, text=True)
        if r.returncode != 0:
            import sys
            sys.stderr.write(f"pdftoppm FAILED ({r.returncode}): {r.stderr}\n")
            sys.stderr.flush()
            raise RuntimeError(r.stderr)
        pngs = sorted(Path(tmp).glob("*.png"))
        for i, png in enumerate(pngs, start=1):
            im = Image.open(png).convert("L")  # e-ink grayscale
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            page_cache[(str(pdf), mtime, i)] = buf.getvalue()
        return len(pngs)


def _refresh(watch_dir: Path, file_arg: str | None) -> None:
    p = _resolve_file(watch_dir, file_arg)
    if p is None:
        state.update(path=None, mtime=0.0, pages=0,
                     name="(no PDF found)", title="(no document)")
        return
    mtime = p.stat().st_mtime
    if state["path"] != str(p) or state["mtime"] != mtime:
        pages = _prerender_all(p, mtime)
        title = _title(p)
        state.update(path=str(p), mtime=mtime, pages=pages,
                     name=p.name, title=title)


def _render_page(pdf: Path, page: int, mtime: float) -> bytes:
    key = (str(pdf), mtime, page)
    if key in page_cache:
        return page_cache[key]
    # Fallback: render a single page on demand.
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf.read_bytes())
        subprocess.run(
            ["pdftoppm", "-r", "226", "-png",
             "-f", str(page), "-l", str(page), str(src), f"{tmp}/p"],
            check=True, capture_output=True)
        pngs = sorted(Path(tmp).glob("*.png"))
        im = Image.open(pngs[0]).convert("L")
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
           display: flex; align-items: baseline; gap: 14px;
           border-bottom: 1px solid #2c2d33; }
  header h1 { font-size: 1rem; font-weight: 600; letter-spacing: .01em; color: #fff;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  header .meta { font-size: .76rem; color: #7d7f86; white-space: nowrap; }
  header .file { font-size: .72rem; color: #5f6168; margin-left: auto;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                 max-width: 38vw; }
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
         padding: 9px 22px; border-radius: 7px; cursor: pointer;
         font-size: .85rem; transition: background .15s; }
  .btn:hover:not(:disabled) { background: #41424a; }
  .btn:disabled { opacity: .3; cursor: default; }
  .page-info { font-size: .85rem; color: #a9abb1; min-width: 110px; text-align: center; }
  .hint { font-size: .72rem; color: #5f6168; }
</style>
</head>
<body>
<header>
  <h1 id="title">reCompose &middot; RM2 Preview</h1>
  <span class="meta" id="meta"></span>
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
  <div class="hint">Images preloaded &middot; arrow keys or buttons to navigate</div>
</div>

<script>
let page = 1, pages = 0, ts = 0;
const img   = document.getElementById('pg');
const info  = document.getElementById('info');
const prev  = document.getElementById('prev');
const next  = document.getElementById('next');
const title = document.getElementById('title');
const meta  = document.getElementById('meta');
const fname = document.getElementById('fname');

async function poll() {
  const r = await fetch('/api/state');
  const s = await r.json();
  if (s.name !== fname.textContent || s.ts !== ts) {
    fname.textContent = s.name || '';
    title.textContent = s.title || 'reCompose · RM2 Preview';
    meta.textContent  = (s.pages ? s.pages + ' pages' : '');
    ts = s.ts; pages = s.pages;
    if (page > pages) page = 1;
    load();
    preload();
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

// Preload every page into the browser cache so navigation is instant.
function preload() {
  for (let i = 1; i <= pages; i++) {
    const im = new Image();
    im.src = '/page?n=' + i + '&t=' + ts;
  }
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
    _refresh(APP_WATCH_DIR, APP_FILE_ARG)
    return {"name": state["name"], "title": state["title"],
            "pages": state["pages"], "ts": state["mtime"]}


@app.get("/page")
async def page(n: int = 1):
    _refresh(APP_WATCH_DIR, APP_FILE_ARG)
    if not state["path"]:
        raise HTTPException(404, "No PDF in watch directory")
    pdf = Path(state["path"])
    n = max(1, min(n, state["pages"]))
    try:
        png = _render_page(pdf, n, state["mtime"])
    except Exception as e:
        raise HTTPException(500, f"render failed: {e}")
    return Response(content=png, media_type="image/png")


def main():
    global APP_WATCH_DIR, APP_FILE_ARG
    ap = argparse.ArgumentParser(description="reCompose RM2 live preview")
    ap.add_argument("--port", type=int, default=7700)
    ap.add_argument("--dir", type=str, default=str(DEFAULT_DIR),
                    help="Directory to watch for the latest *.pdf")
    ap.add_argument("--file", type=str, default=None,
                    help="Pin a document: path, or substring/glob inside --dir "
                         "(latest match by mtime). Default: latest *.pdf in --dir")
    args = ap.parse_args()
    APP_WATCH_DIR = Path(args.dir).expanduser()
    APP_FILE_ARG = args.file
    print(f"  reCompose RM2 preview -> http://localhost:{args.port}")
    print(f"  watching: {APP_WATCH_DIR}")
    if APP_FILE_ARG:
        print(f"  pinned file filter: {APP_FILE_ARG}")
    uvicorn_run(app, args.port)


# Lazy import uvicorn so --help works without it installed.
def uvicorn_run(app, port):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
