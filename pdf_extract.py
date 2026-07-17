#!/usr/bin/env python3
"""pdf_extract.py — Extract text, tables, figures, and math from an academic PDF.

Uses Gemini Vision (google/gemini-2.5-flash via OpenRouter) to extract structured
content page-by-page, then reassembles into clean Markdown suitable for the
reCompose pipeline (pandoc + rm2.latex → reMarkable 2 PDF).

Designed for academic papers and theses:
- Math is returned as LaTeX ($...$ inline, $$...$$ display)
- Multi-column layouts are linearised correctly (left column then right)
- Chapter/section hierarchy is preserved
- Footnotes are collected at the end of each page's text
- Running headers, footers, and page numbers are stripped

Output layout:
  <outdir>/
    text.md          — prose + math + figure placeholders
    tables.md        — all tables as markdown
    figures/
      fig_pNN.png    — cropped figure images (200 DPI)
    extracted.json   — raw API responses (debug)

Usage:
  python3 pdf_extract.py paper.pdf [--out <outdir>] [--key <key>] [--dpi 200]
  python3 pdf_extract.py thesis.pdf --thesis   # chapter-aware mode

Environment:
  OPENROUTER_API_KEY  — or pass via --key, or reads from ~/.secrets.env

Requires:
  pip install pymupdf pillow
  apt install poppler-utils
"""

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.request

import fitz  # pymupdf
from PIL import Image


# ── API ──────────────────────────────────────────────────────────────────────

def _load_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    if k := os.environ.get("OPENROUTER_API_KEY"):
        return k
    secrets = pathlib.Path("~/.secrets.env").expanduser()
    if secrets.exists():
        for line in secrets.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("No OPENROUTER_API_KEY found. Pass --key or set env var.")


def _gemini(api_key: str, image_path: pathlib.Path, prompt: str) -> dict:
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    payload = json.dumps({
        "model": "google/gemini-2.5-flash",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return json.loads(data["choices"][0]["message"]["content"])


# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are transcribing one page of an academic PDF into structured JSON. Your job is faithful, accurate extraction — do NOT rewrite, reformat, or editorialize. Reproduce content exactly as it appears.

DEVICE CONTEXT: The page images you receive are rendered at 226 DPI — the native resolution of the reMarkable 2 e-ink tablet (1872 × 1404 px, 263 PPI). This is a HIGH-RESOLUTION display. Images, figures, and diagrams that appear sharp in these renders WILL appear equally sharp on the actual device. Do NOT penalize or downgrade figure/image content based on e-ink resolution assumptions.

CRITICAL RULES:
1. All mathematical expressions MUST be returned as LaTeX:
   - Inline math: $x = y + z$ (single dollar signs)
   - Display/block equations: $$E = mc^2$$ (double dollar signs)
   - Never describe equations in prose — reproduce them as LaTeX
2. If the page has TWO COLUMNS, read left column fully first, then right column. Linearise into a single flow.
3. Strip: page numbers, running headers (journal name, author name, chapter title repeated at top), and footers.
4. Preserve: footnotes — append them at the end of the text field as "^[footnote text]" inline markers.
5. Do NOT convert prose to lists. Do NOT bold words that were not bold in the original. Do NOT split or merge paragraphs. Transcribe what is there.

HEADING HIERARCHY — follow strictly:
- # (h1): Paper title only, and only if this is the title page. Never use # for section headings.
- ## (h2): Top-level numbered sections. PRESERVE the section number: "2 Introduction" → ## 2 Introduction
- ### (h3): Subsections. PRESERVE the number: "2.1 Background" → ### 2.1 Background
- #### (h4): Sub-subsections: 1.1.1, A.1.2, etc.
Appendix subsections (A.1, B.3, etc.) MUST be ### not ##. Do not promote them.

FIGURES — do NOT include figure content or captions inline in "text".
Instead, place a placeholder exactly like this at the position where the figure appears:
  [FIGURE: Figure 1]
where "Figure 1" matches the label. The caption and description go only in the "figures" list.
Caption text must be plain prose — no bold, no italic markdown formatting.

Return a JSON object with these keys:
- "page_type": one of "title", "abstract", "body", "figure", "table", "references", "appendix", "mixed"
- "title": the paper title string if this is the title page, else null
- "text": all prose and math. Figures replaced with [FIGURE: label] placeholders. Use heading hierarchy above.
  Superscript citations as [N] or [AuthorYear]. Inline math as $...$. Display equations as $$...$$.
- "tables": list of {"caption": "...", "markdown": "<full github-flavoured markdown table>"}. Empty list if none.
  Reproduce ALL cells accurately, including numeric values. Use --- alignment rows.
- "figures": list of {"label": "Figure 1", "caption": "<full plain-text caption>", "description": "<one-line description>"}. Empty list if none.
- "has_figure_image": true if the page contains a chart, diagram, photograph, or plotted figure."""

THESIS_EXTRACT_PROMPT = EXTRACT_PROMPT.replace(
    "- # (h1): Paper title only, and only if this is the title page. Never use # for section headings.",
    "- # (h1): Chapter titles: 'Chapter 3: Methodology' → # Chapter 3: Methodology. Also paper title on title page.",
)

REFERENCES_PROMPT = """Extract all reference entries from this references/bibliography page.

Return JSON:
{
  "page_type": "references",
  "text": "<all references as a markdown list, one entry per line, starting with - . Preserve author names, year, title, journal/publisher, volume, pages, DOI exactly>",
  "tables": [],
  "figures": [],
  "has_figure_image": false
}

Do not truncate. Include every reference visible on the page."""

ABSTRACT_PROMPT = """Extract the abstract from this page.

Return JSON:
{
  "page_type": "abstract",
  "text": "## Abstract\\n\\n<abstract text verbatim, preserving any mathematical expressions as LaTeX $...$ or $$...$$>",
  "tables": [],
  "figures": [],
  "has_figure_image": false
}"""


def _choose_prompt(n: int, n_pages: int, page_type_hint: str | None,
                   thesis: bool) -> str:
    if page_type_hint == "abstract":
        return ABSTRACT_PROMPT
    # Heuristic: last ~10% of pages are likely references
    if n >= n_pages - max(2, n_pages // 10):
        return REFERENCES_PROMPT
    return THESIS_EXTRACT_PROMPT if thesis else EXTRACT_PROMPT


# ── Figure cropping ───────────────────────────────────────────────────────────

def _crop_figure(pdf_path: pathlib.Path, page_idx: int, render_dpi: int,
                 page_png: pathlib.Path, out_path: pathlib.Path) -> None:
    """Crop figure region from page using pymupdf text block positions.

    Strategy: find the first text block whose content starts with "Figure", "Fig.",
    or "FIGURE" — that marks the caption boundary. The figure occupies the space
    above it. Falls back to top 45% of page if no caption is detected.

    NOTE: pymupdf coordinates are in PDF points (72 DPI). Scale by (render_dpi/72)
    to convert to pixel space before cropping the rendered PNG.
    """
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    ph = page.rect.height  # pts
    scale = render_dpi / 72.0

    blocks = sorted(page.get_text("blocks"), key=lambda b: b[1])  # sort by y0
    cap_y = None
    for b in blocks:
        txt = b[4].strip().lower()
        if txt.startswith(("figure", "fig.", "figure ")):
            cap_y = b[1]  # top of caption block (pts)
            break

    fig_y0_pts = 36  # ~0.5in top margin
    fig_y1_pts = cap_y if cap_y else ph * 0.45

    img = Image.open(page_png)
    W = img.size[0]
    px_y0 = int(fig_y0_pts * scale)
    px_y1 = int(fig_y1_pts * scale)
    cropped = img.crop((0, px_y0, W, px_y1))
    cropped.save(out_path, optimize=True)
    doc.close()


# ── Page type pre-detection ───────────────────────────────────────────────────

def _sniff_page_type(pdf_path: pathlib.Path, page_idx: int) -> str | None:
    """Quick pymupdf text scan to hint at page type before calling Gemini."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    text = page.get_text().strip().lower()
    doc.close()
    if page_idx == 0:
        return None  # title page — use default prompt
    if text.startswith("abstract"):
        return "abstract"
    return None


# ── Markdown post-processing ──────────────────────────────────────────────────

def _fix_math_spans(text_md: str) -> str:
    r"""Fix math and currency $ issues so pandoc + XeLaTeX don't misparse.

    Pandoc inline math rules for $...$:
    - Opening $ must not be followed by whitespace
    - Closing $ must not be preceded by whitespace
    - Closing $ must not be followed by a digit

    Strategy:
    1. Protect valid math spans (content contains \) with a placeholder
    2. Escape remaining bare $DIGIT as currency: \$
    3. Restore math spans
    4. Fix closing $ before digit inside math: $\cmd$DIGIT → $\cmd DIGIT$
    """
    # Step 1: protect valid math spans $\latex...$ (any span containing a backslash)
    protected: dict[str, str] = {}
    counter = [0]

    def _protect(m: re.Match) -> str:
        content = m.group(1)
        if '\\' in content:
            key = f'__MATH{counter[0]}__'
            protected[key] = m.group(0)
            counter[0] += 1
            return key
        return m.group(0)

    # Match non-greedy single-line math spans
    text_md = re.sub(r'\$([^$\n]+?)\$', _protect, text_md)

    # Step 2: escape remaining $ before digits — these are currency
    text_md = re.sub(r'(?<!\\)\$(?=\d)', r'\\$', text_md)

    # Step 3: restore protected math spans
    for key, val in protected.items():
        text_md = text_md.replace(key, val)

    # Step 4: fix closing $ immediately before a digit inside a math span
    # e.g. $\sim$1.2k → $\sim 1.2k$ (pulls digit sequence into the span)
    # Only for short spans starting with \ to avoid false positives
    text_md = re.sub(r'\$(\\[^$]{1,40}?)\$(\d[\d.,a-zA-Z]*)', r'$\1 \2$', text_md)

    return text_md


def _truncate_wide_table(md_table: str, max_cols: int = 4) -> str:
    """Truncate a GFM table to max_cols columns if it is wider.

    The reMarkable 2 text column is 138mm. Tables wider than 4 columns
    overflow regardless of font size. We keep columns 0..max_cols-1 and
    append a note row advising the reader to see the original.
    """
    lines = [l for l in md_table.splitlines() if l.strip()]
    if not lines:
        return md_table
    # Count columns in the header row (number of | separators - 1)
    header_cols = lines[0].count('|') - 1
    if header_cols <= max_cols:
        return md_table

    result = []
    for line in lines:
        if not line.strip().startswith('|'):
            result.append(line)
            continue
        cells = line.split('|')
        # cells[0] is empty (before leading |), cells[-1] is empty (after trailing |)
        # Keep cells[1 : max_cols+1], rebuild
        kept = cells[1 : max_cols + 1]
        # For separator rows (--- cells), truncate cleanly
        result.append('| ' + ' | '.join(c.strip() for c in kept) + ' |')
    result.append(f'\n*Table condensed to {max_cols} columns — '
                  'see original PDF for full data.*')
    return '\n'.join(result)


def _clean_text(text_md: str) -> str:
    # Remove orphan page numbers (standalone digit lines)
    text_md = re.sub(r'^\s*\d{1,4}\s*$', '', text_md, flags=re.MULTILINE)
    # Fix math delimiter issues before pandoc sees the markdown
    text_md = _fix_math_spans(text_md)
    # Collapse excess blank lines
    text_md = re.sub(r'\n{3,}', '\n\n', text_md)
    return text_md.strip()


# ── Main extraction ───────────────────────────────────────────────────────────

def extract(pdf_path: pathlib.Path, outdir: pathlib.Path,
            api_key: str, render_dpi: int = 226, thesis: bool = False) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    figdir = outdir / "figures"
    figdir.mkdir(exist_ok=True)

    # Render all pages to PNG
    render_stem = str(outdir / "page")
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(render_dpi), str(pdf_path), render_stem],
        check=True, capture_output=True,
    )
    page_pngs = sorted(outdir.glob("page-*.png"))
    n_pages = len(page_pngs)
    print(f"{pdf_path.name}: {n_pages} pages at {render_dpi} DPI")

    # Extract content page-by-page
    results = []
    for png in page_pngs:
        n = int(png.stem.split("-")[1])
        hint = _sniff_page_type(pdf_path, n - 1)
        prompt = _choose_prompt(n, n_pages, hint, thesis)
        try:
            parsed = _gemini(api_key, png, prompt)
            parsed["page"] = n
            figs = parsed.get("figures", [])
            tbls = parsed.get("tables", [])
            has_img = parsed.get("has_figure_image", False)
            print(f"  p{n:02d}: {parsed.get('page_type','?'):12s} | "
                  f"tables={len(tbls)} figs={len(figs)} img={has_img}")
            results.append(parsed)
        except Exception as e:
            print(f"  p{n:02d}: ERROR {e}")
            results.append({"page": n, "error": str(e),
                            "text": "", "tables": [], "figures": [],
                            "has_figure_image": False})
        time.sleep(0.3)

    # Crop figure images
    for page in results:
        n = page["page"]
        if page.get("has_figure_image"):
            png = outdir / f"page-{n:02d}.png"
            if png.exists():
                dst = figdir / f"fig_p{n:02d}.png"
                try:
                    _crop_figure(pdf_path, n - 1, render_dpi, png, dst)
                    print(f"  cropped fig_p{n:02d}.png → {dst.stat().st_size // 1024}KB")
                except Exception as e:
                    print(f"  fig crop p{n:02d}: {e}")

    # Build figure label → page map for placeholder resolution
    fig_index: dict[str, dict] = {}  # label (lower) → {page, caption, description}
    for page in results:
        n = page["page"]
        for fig in page.get("figures", []):
            label = fig.get("label", "")
            fig_index[label.lower()] = {"page": n, "caption": fig.get("caption", ""),
                                        "description": fig.get("description", "")}

    def _resolve_placeholder(m: re.Match) -> str:
        """Replace [FIGURE: Figure 1] with image markdown or italicised description."""
        label = m.group(1).strip()
        info = fig_index.get(label.lower(), {})
        n = info.get("page")
        caption = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', info.get("caption", label))
        if n:
            fig_file = figdir / f"fig_p{n:02d}.png"
            if fig_file.exists():
                return f"\n\n![{label}](figures/fig_p{n:02d}.png)\n\n*{caption}*\n"
        return f"\n\n*[{label}: {info.get('description', caption)}]*\n"

    # Assemble text.md — figures come from placeholder substitution only (no duplicate append)
    text_parts = []
    for page in sorted(results, key=lambda p: p["page"]):
        txt = page.get("text", "").strip()
        if txt:
            text_parts.append(txt)

    raw = "\n\n".join(text_parts)
    # Resolve [FIGURE: ...] placeholders
    raw = re.sub(r'\[FIGURE:\s*([^\]]+)\]', _resolve_placeholder, raw)

    # Extract paper title from page 1 for YAML frontmatter
    paper_title: str | None = None
    for page in results:
        if page.get("page") == 1:
            paper_title = page.get("title") or None
            break
    if not paper_title:
        # Fallback: grab first # heading from text
        m = re.search(r'^#\s+(.+)$', raw, re.MULTILINE)
        if m:
            paper_title = m.group(1).strip()
    # Strip # headings from body (title goes to YAML frontmatter)
    raw = re.sub(r'^#\s+.+\n?', '', raw, flags=re.MULTILINE)

    # Detect TOC depth: if >12 ## sections (papers with large appendices), use depth 1
    h2_count = len(re.findall(r'^##\s', raw, re.MULTILINE))
    toc_depth = 1 if h2_count > 12 else 2

    # Write frontmatter.yaml for recompose build to pick up
    frontmatter_lines = []
    if paper_title:
        frontmatter_lines.append(f'title: "{paper_title}"')
    frontmatter_lines.append(f'toc-depth: {toc_depth}')
    (outdir / "frontmatter.yaml").write_text("\n".join(frontmatter_lines) + "\n")

    text_md = _clean_text(raw)
    (outdir / "text.md").write_text(text_md)

    # Assemble tables.md (truncate wide tables to 4 cols for 138mm RM2 column)
    table_parts = []
    for page in sorted(results, key=lambda p: p["page"]):
        for tbl in page.get("tables", []):
            cap = tbl.get("caption", f"Table (page {page['page']})")
            md = _truncate_wide_table(tbl.get("markdown", "").strip())
            if md:
                table_parts.append(f"### {cap}\n\n{md}")
    if table_parts:
        (outdir / "tables.md").write_text("\n\n---\n\n".join(table_parts))

    # Save raw JSON for debugging
    (outdir / "extracted.json").write_text(json.dumps(results, indent=2))

    # Summary
    crops = list(figdir.glob("*.png"))
    print(f"\nOutput: {outdir}/")
    print(f"  text.md    ({len(text_md.splitlines())} lines)")
    if table_parts:
        print(f"  tables.md  ({len(table_parts)} tables)")
    if crops:
        print(f"  figures/   ({len(crops)} crops)")
    print(f"  extracted.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="Output directory (default: <pdf-stem>_extracted/)")
    ap.add_argument("--key", default=None, help="OpenRouter API key")
    ap.add_argument("--dpi", type=int, default=226, help="Render DPI (default 226 — RM2 native)")
    ap.add_argument("--thesis", action="store_true",
                    help="Thesis mode: chapter-aware heading hierarchy")
    args = ap.parse_args()

    pdf = args.pdf.resolve()
    outdir = args.out or pdf.parent / (pdf.stem + "_extracted")
    api_key = _load_key(args.key)
    extract(pdf, outdir, api_key, render_dpi=args.dpi, thesis=args.thesis)


if __name__ == "__main__":
    main()
