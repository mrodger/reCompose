# reCompose

Full round-trip pipeline for the **reMarkable 2** e-ink tablet: extract structured content from academic PDFs and reformat them for the RM2 canvas, or convert your own Markdown directly to e-ink-optimised PDFs.

## Screenshots

| Tech doc (TOC + tables) | Appendix with image wrap | Novel prose |
|---|---|---|
| ![tech doc](screenshots/tech-doc.png) | ![appendix](screenshots/appendix-stoicism.png) | ![prose](screenshots/prose-novel.png) |

---

Convert Markdown documents to PDFs optimised for the **reMarkable 2** e-ink display.

The reMarkable 2 renders at 1404 × 1872 px on a 10.3″ grayscale screen. Standard A4/Letter PDFs are illegible without pinch-zoom. reCompose pre-formats the page to the exact RM2 canvas so documents are readable at native scale with no on-device adjustment.

---

## How it works

```
document.md
  │
  ▼  [1] Pandoc + rm2.latex template
document.tex
  │
  ▼  [2] fix_tables.py  (longtable → xltabular, p{} cols → X)
document.tex  (patched)
  │
  ▼  [2.5] Bibliography spacing — tighten \parskip/\setstretch in the refs block
document.tex  (patched)
  │
  ▼  [3] XeLaTeX
document.pdf  (157.8 mm × 210.4 mm, grayscale-optimised)
```

Three passes (plus a small [2.5] post-processing step). One command.

The `[2.5]` step runs a `sed` pass that wraps the bibliography block in a
`\begingroup` / `\endgroup` with tighter `\parskip` and `\setstretch{1.25}`,
so references don't sprawl on the small RM2 page before the final XeLaTeX run.

---

## Requirements

| Tool | Notes |
|---|---|
| [pandoc](https://pandoc.org/) | 3.0+ recommended |
| xelatex | `texlive-xetex` or MacTeX |
| Linux Libertine O fonts | `fonts-linuxlibertine` (Debian/Ubuntu) or [download](https://sourceforge.net/projects/linuxlibertine/) |
| `texlive-latex-extra` | Provides `xltabular`, `ltablex` |
| Python 3.8+ | For `fix_tables.py` |

### Install on Ubuntu/Debian

```bash
sudo apt install pandoc texlive-xetex texlive-latex-extra fonts-linuxlibertine python3
```

### Install on macOS

```bash
brew install pandoc
# Install MacTeX: https://tug.org/mactex/
# Linux Libertine: download from sourceforge and install via Font Book
```

---

## Usage

Place your Markdown file(s) in the same directory as `rm2.latex`, `fix_tables.py`, and the `Makefile`.

```bash
# Build all .md files
make

# Build a single file
make my-document.pdf

# Clean build artifacts
make clean

# Rebuild everything from scratch
make rebuild
```

The output PDF is sized to **157.8 mm × 210.4 mm** — the exact reMarkable 2 viewable area at 226 DPI. Transfer to the device using [rmapi](https://github.com/juruen/rmapi), [reMarkable CLI](https://github.com/juruen/rmapi), or the official desktop app.

### Optional: upload via rclone

If you have rclone configured with a Google Drive remote:

```bash
# Edit GDRIVE_DEST in the Makefile to match your remote, then:
make upload
```

---

## Markdown front matter

Supported pandoc YAML variables:

```yaml
---
title: My Document
subtitle: A subtitle
author: Your Name
date: 2026-01-01
---
```

All standard pandoc Markdown features work: headings, tables, fenced code blocks, lists, blockquotes, footnotes, images, and math (`$$`).

---

## Design decisions

### Page geometry

157.8 mm × 210.4 mm (1404 px ÷ 226 DPI × 25.4 mm, rounded up). 10 mm margins on all sides. The RM2 PDF viewer does not reliably crop margins — the document must be pre-sized to the canvas.

### Fonts

| Role | Font |
|---|---|
| Body | Linux Libertine O 11 pt — high x-height, open counters, excellent e-ink legibility |
| Headings | Linux Biolinum O Bold — sans-serif contrast against the serif body |
| Monospace | Linux Libertine Mono O — matches family x-height |

Computer Modern was rejected: strokes too thin, washes out on e-ink.

### Grayscale palette

E-ink has no colour gamut. All elements map to gray values:

- Body text: black (0.0)
- Links: near-black (0.15) — visually distinct without colour
- Table rules: mid-gray (0.55)
- Code background: very light gray (0.91)
- Page numbers: faded (0.40)

### Table handling

Pandoc generates `longtable` with fixed `p{...}` column widths calculated from Markdown proportions. On the 138 mm RM2 text column, these become too narrow for readable wrapping. `fix_tables.py` replaces the fixed column specs with auto-sizing `X` columns via `xltabular` (which combines `longtable` page-breaking with `tabularx` proportional sizing).

The replacement requires stack-based brace matching (Python) because the pandoc column spec expressions contain nested braces that `sed` cannot handle:

```
>{\raggedright\arraybackslash}p{(\columnwidth - 8\tabcolsep) * \real{0.25}}
```

becomes `X`.

### Typography

- Line stretch: 1.15 (prevents line blending on e-ink)
- Paragraph spacing: 5 pt (block-paragraph style, no indent)
- Widow/orphan penalty: 10000 (no isolated lines on small pages)
- `\emergencystretch`: 2 em (prevents overfull hboxes in the narrow column)
- `\tolerance=400` (looser line-breaking for the 137 mm column)

---

## Extracting from academic PDFs

`pdf_extract.py` converts an academic PDF (paper, thesis, report) into clean Markdown suitable for the reCompose pipeline. It uses Gemini Vision page-by-page, then assembles the output.

```bash
# pip install pymupdf pillow
# apt install poppler-utils
# export OPENROUTER_API_KEY=sk-or-...

# Extract a paper
python3 pdf_extract.py paper.pdf

# Thesis mode (chapter-aware headings)
python3 pdf_extract.py thesis.pdf --thesis --out thesis_extracted/
```

Output in `<stem>_extracted/`:
- `text.md` — prose + LaTeX math + figure placeholders
- `tables.md` — all tables as Markdown
- `figures/` — cropped figure images at 200 DPI
- `extracted.json` — raw API responses for debugging

Then build:

```bash
cp paper_extracted/text.md build/
cd build && make text.pdf
```

Or use `recompose.py` for the full round-trip in one command (see below).

### Academic PDF notes

- **Math** is returned as LaTeX (`$...$` inline, `$$...$$` display) — not prose descriptions.
- **Two-column layouts** (journal format) are linearised: left column then right.
- **Theses** often have complex LaTeX-generated structure. Use `--thesis` to get chapter-level `#` headings. Section numbering is stripped from headings by default; add `numbersections: true` to the YAML frontmatter if you want them in the output.
- **Figures** are cropped using pymupdf text block positions to locate the caption boundary, not Gemini bounding boxes (which are in PDF point space, not pixel space).
- **References** pages use a lighter prompt that captures full citation strings.

---

## Round-trip with recompose.py

`recompose.py` is a deterministic CLI wrapper over the full pipeline. No LLM is needed for the Markdown → PDF build step; Gemini is only called during extraction.

```bash
# Extract only
python3 recompose.py extract paper.pdf

# Build only (from existing markdown)
python3 recompose.py build paper_extracted/text.md

# Full round-trip: extract → build → upload to GDrive
python3 recompose.py roundtrip paper.pdf --upload

# Round-trip a thesis, custom output name, no GDrive upload
python3 recompose.py roundtrip thesis.pdf --thesis --rename "Smith2024-RM" --no-upload
```

---

## Layout recipes

### Appendix page with image wrap

```markdown
---
title: "Book Title"
toc: false
header-includes: |
  \usepackage{wrapfig}
  \usepackage{textcase}
  \fancyfoot[L]{\sffamily\footnotesize\color{gray!60}BOOK TITLE \textbullet\ APPENDIX A}
  \fancyfoot[R]{\sffamily\footnotesize\color{gray!60}\thepage}
  \fancyfoot[C]{}
  \newcommand{\appendixlabel}[2]{%
    {\sffamily\footnotesize\bfseries\MakeUppercase{Appendix #1}}\par\vspace{2pt}%
    {\rmfamily\Huge\bfseries #2}\par\vspace{12pt}%
  }
---

\appendixlabel{A}{Chapter Title}

\begin{wrapfigure}{r}{0.35\textwidth}
\includegraphics[width=0.35\textwidth]{image.jpg}
\par\vspace{4pt}
{\sffamily\footnotesize Image caption here}
\end{wrapfigure}

\textsc{First word} of the appendix text...
```

### Novel prose (indented paragraphs, ragged-right, custom footer)

```markdown
---
toc: false
header-includes: |
  \usepackage{ragged2e}
  \RaggedRight
  \fancyfoot[L]{\sffamily\footnotesize\color{gray!60}BOOK TITLE \textbullet\ CHAPTER VII}
  \fancyfoot[R]{\sffamily\footnotesize\color{gray!60}142}
  \fancyfoot[C]{}
  \AtBeginDocument{\setlength{\parindent}{1.5em}\setlength{\parskip}{0pt}}
---
```

> Omit `title:` from frontmatter to prevent `\maketitle` calling `\thispagestyle{plain}` on page 1, which would override the custom footer.

### Section numbering (off by default)

```yaml
---
title: "Technical Report"
numbersections: true
secnumdepth: 3
---
```

---

## Live preview

`rm2_preview.py` serves a live, browser-based **reMarkable 2 device frame** on
`http://localhost:7700`. It shows a chosen PDF inside a clean CSS device frame
(visible bezel, rounded corners, e-ink grayscale). Every page is pre-rendered
into a memory cache and preloaded by the browser, so Prev/Next navigation is
instant.

```bash
# Pin a specific document (substring match inside the watch dir;
# the latest match by mtime wins). Shows title + page count in the header.
python3 rm2_preview.py --file lyzr-platform-analysis-v2

# Or just serve the most recently built PDF in the watch dir:
python3 rm2_preview.py

# Custom port / custom directory:
python3 rm2_preview.py --port 8080 --dir /path/to/output
```

- The header shows the **document title** (extracted from page 1), the
  **page count**, and the source filename.
- Navigate with the **Prev / Next** buttons or **arrow keys**.
- Pages **fill the screen exactly** — no grey ring, no distortion.

There is also a convenience launcher that runs the server as a persistent
background process (logs to `~/rm2_preview.log`):

```bash
./start_preview.sh                       # latest built PDF
./start_preview.sh lyzr-platform-analysis-v2   # pin a document
# Stop it with:  pkill -f rm2_preview.py
```

> **Why a CSS frame instead of the device photo?** The CC-licensed RM2 photo on
> Wikimedia is a "screen-on" flat-lay where the display is a transparent window
> onto the grey surface behind the tablet. Compositing a report into that photo
> leaves a grey ring (the surface showing through) and the corners never align.
> The drawn frame gives pixel-accurate alignment and a proper bezel. Use
> `rm2_mockup.py` when you specifically want a still composited into the real
> device photo for sharing.

Requires `fastapi`, `uvicorn`, `pillow`, and `poppler-utils` (`pdftoppm`).
Page counts for both the preview and the mockup are derived from `pdftoppm`
rendered pages, so `pdfinfo` is no longer needed.

```bash
pip install fastapi uvicorn pillow
sudo apt install poppler-utils
```

---

## Device mockup

`rm2_mockup.py` composites a built PDF (or any PNG) into a photograph of a real RM2, so you can see how a page looks on the actual device before transferring it.

```bash
# pip install Pillow  (poppler-utils for PDF input)

# Single page
python3 rm2_mockup.py my-document.pdf

# Specific page, full-size output
python3 rm2_mockup.py my-document.pdf --page 3 --scale 1.0 --out page3_preview.png

# All pages as separate images
python3 rm2_mockup.py my-document.pdf --all-pages
```

Output is a PNG of the device photo with your page composited into the screen area, converted to grayscale to simulate e-ink rendering. Pass `--no-grayscale` to keep colour.

The device photo (`rm2_device.jpg`) is by [Axel Dgn](https://commons.wikimedia.org/wiki/File:Remarkable_2_with_standard_marker.jpg), [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), Wikimedia Commons.

---

## File map

```
reCompose/
├── recompose.py     # CLI: extract / build / roundtrip
├── pdf_extract.py   # PDF → markdown + figures + tables (Gemini Vision)
├── fix_tables.py    # longtable → xltabular transformer
├── rm2.latex        # Pandoc LaTeX template
├── Makefile         # Three-pass build automation
├── rm2_preview.py   # Live browser preview (CSS device frame, preloaded pages)
├── start_preview.sh # Launcher: run the preview as a background service
├── rm2_mockup.py    # Composite PDF pages into device photo
├── rm2_device.jpg   # CC BY-SA 4.0 device photo (Wikimedia Commons)
├── screenshots/     # Example output images
├── example/
│   └── example.md   # Sample document demonstrating all features
└── README.md
```

---

## Known limitations

- **No colour syntax highlighting** — code blocks are grayscale framed.
- **No working hyperlinks** — RM2's PDF viewer does not follow links. URLs are visually distinguished but non-functional.
- **No CJK support** — Linux Libertine covers Latin, Cyrillic, and Greek only.
- **Very wide tables (> 6 columns)** — even proportional `X` columns get cramped at 138 mm. Split such tables in source or consider landscape pages.
- **Images** — included images render in grayscale. Pre-process with `convert -colorspace Gray` for best results.
- **One-way pipeline** — this produces read-only PDFs. Annotations made on the RM2 are not reflected back.

---

## License

MIT — see [LICENSE](LICENSE).
