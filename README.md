# reCompose

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
  ▼  [3] XeLaTeX
document.pdf  (157.8 mm × 210.4 mm, grayscale-optimised)
```

Three passes. One command.

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
├── rm2.latex        # Pandoc LaTeX template
├── fix_tables.py    # longtable → xltabular transformer
├── Makefile         # Three-pass build automation
├── rm2_mockup.py    # Composite PDF pages into device photo
├── rm2_device.jpg   # CC BY-SA 4.0 device photo (Wikimedia Commons)
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
