---
title: reCompose Example
subtitle: All supported features in one document
author: reCompose
date: 2026-01-01
---

# Headings

Section headings use Linux Biolinum (sans-serif) for contrast against the Libertine serif body. The `\section` rule provides a visual break without colour.

## Subsection

Subsection headings drop the rule but keep the bold sans weight.

### Subsubsection

Italic bold — still clearly subordinate.

# Body text

Body copy in Linux Libertine O at 11 pt with 1.15 line stretch. The narrow 138 mm column is wider than a typical mobile screen but narrower than A4. Paragraph spacing is 5 pt with no indent — block-paragraph style.

Long paragraphs reflow gracefully. The `\tolerance=400` setting allows slightly looser line-breaking to prevent overfull hboxes in the narrow column. Emergency stretch (`\emergencystretch=2em`) catches any remaining outliers.

# Lists

Unordered:

- Item one
- Item two with a longer description that wraps to demonstrate how list text reflows in the narrow column
- Item three

Ordered:

1. First step
2. Second step
3. Third step

Description list:

Term one
:   Definition for term one. Can span multiple sentences.

Term two
:   Definition for term two.

# Tables

Tables are the most complex element. `fix_tables.py` converts pandoc's fixed `p{}` columns to proportional `X` columns via `xltabular`.

| Tool       | Purpose                        | Required |
|------------|-------------------------------|----------|
| pandoc     | Markdown → LaTeX              | Yes      |
| xelatex    | LaTeX → PDF                   | Yes      |
| fix_tables | longtable → xltabular         | Yes      |
| rclone     | Upload to Google Drive        | Optional |

Wide table — six columns:

| Month | Jan | Feb | Mar | Apr | May | Jun |
|-------|-----|-----|-----|-----|-----|-----|
| Rain  | 82  | 75  | 70  | 61  | 53  | 48  |
| Temp  | 19  | 19  | 17  | 15  | 12  | 10  |

# Code

Inline code: `fix_tables.py` rewrites `longtable` environments in the XeLaTeX source.

Fenced block:

```python
def _find_matching_brace(s: str, start: int) -> int:
    """Stack-based brace matcher for nested LaTeX expressions."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '{':
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"Unmatched brace at {start}")
```

Shell command:

```bash
make my-document.pdf
```

# Blockquote

> The reMarkable 2 renders at 226 DPI on a 10.3 inch grayscale screen. Standard A4 PDFs require pinch-zoom to read. Pre-sizing the canvas eliminates that friction entirely.

# Math

Inline: the text column is $w = 137.8\,\text{mm}$.

Display:

$$
\text{DPI} = \frac{\text{pixels}}{\text{inches}} = \frac{1404}{6.2} \approx 226
$$

# Images

Images are scaled to fit the text column (`width=\textwidth`) with aspect ratio preserved. E-ink renders all images in grayscale regardless of the source — pre-process with:

```bash
convert -colorspace Gray input.png output.png
```

# Footnotes

Body text with a footnote.[^1] Footnotes render at the bottom of the page in a smaller font.

[^1]: Footnotes are supported via standard pandoc syntax. They appear at page bottom, not chapter end.

# Horizontal rule

---

Rules render as a thin line across the text column.

# Conclusion

This document exercises every major feature supported by the reCompose pipeline. Build it with:

```bash
make example/example.pdf
```

Verify the page size:

```bash
pdfinfo example/example.pdf | grep "Page size"
# Page size:      447.31 x 596.41 pts
# = 157.8 mm x 210.4 mm ✓
```
