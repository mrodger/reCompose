#!/usr/bin/env python3
"""recompose — reMarkable 2 PDF pipeline.

Deterministic CLI wrapper over the reCompose toolchain. No LLM is invoked
during the build step; Gemini Vision is only called during extract.

Subcommands:
  extract    PDF → markdown + figures + tables (requires OpenRouter key)
  build      markdown → RM2 PDF via pandoc + fix_tables.py + xelatex
  roundtrip  extract then build (full pipeline)

Usage:
  python3 recompose.py extract paper.pdf [--out DIR] [--dpi 200] [--thesis]
  python3 recompose.py build   text.md   [--no-toc] [--rename NAME]
  python3 recompose.py roundtrip paper.pdf [--out DIR] [--thesis]
                                           [--rename NAME] [--upload]
                                           [--no-upload] [--gdrive DEST]
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


# ── Helpers ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()


def _require(cmd: str) -> None:
    if not shutil.which(cmd):
        sys.exit(f"error: '{cmd}' not found. Install it and retry.")


def _run(args: list, cwd: pathlib.Path | None = None, fatal: bool = True) -> int:
    result = subprocess.run(args, cwd=cwd)
    if fatal and result.returncode != 0:
        sys.exit(f"error: command failed: {' '.join(str(a) for a in args)}")
    return result.returncode


def _load_openrouter_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    if k := os.environ.get("OPENROUTER_API_KEY"):
        return k
    secrets = pathlib.Path("~/.secrets.env").expanduser()
    if secrets.exists():
        for line in secrets.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("error: OPENROUTER_API_KEY not set. Pass --key or export the variable.")


# ── extract ───────────────────────────────────────────────────────────────────

def cmd_extract(args: argparse.Namespace) -> pathlib.Path:
    """Extract content from a PDF. Returns the output directory."""
    _require("pdftoppm")
    pdf = pathlib.Path(args.pdf).resolve()
    if not pdf.exists():
        sys.exit(f"error: file not found: {pdf}")

    outdir = pathlib.Path(args.out).resolve() if args.out else pdf.parent / (pdf.stem + "_extracted")
    api_key = _load_openrouter_key(getattr(args, "key", None))

    # Import and call directly (same process — avoids subprocess overhead and
    # lets the caller capture the outdir path without parsing stdout).
    sys.path.insert(0, str(SCRIPT_DIR))
    from pdf_extract import extract
    extract(pdf, outdir, api_key,
            render_dpi=getattr(args, "dpi", 200),
            thesis=getattr(args, "thesis", False))
    return outdir


# ── build ─────────────────────────────────────────────────────────────────────

def cmd_build(args: argparse.Namespace) -> pathlib.Path:
    """Build a Markdown file into an RM2 PDF. Returns the output PDF path."""
    _require("pandoc")
    _require("xelatex")

    src = pathlib.Path(args.md).resolve()
    if not src.exists():
        sys.exit(f"error: file not found: {src}")

    # Work in a temp dir so intermediate .tex/.aux files don't pollute the source dir.
    with tempfile.TemporaryDirectory(prefix="recompose-") as tmp:
        build_dir = pathlib.Path(tmp)

        # Copy pipeline assets + source
        for asset in ("rm2.latex", "fix_tables.py", "Makefile"):
            shutil.copy2(SCRIPT_DIR / asset, build_dir / asset)
        shutil.copy2(src, build_dir / "source.md")

        # Copy figures dir alongside if it exists (pdf_extract output)
        fig_dir = src.parent / "figures"
        if fig_dir.is_dir():
            shutil.copytree(fig_dir, build_dir / "figures")

        # Patch Makefile: remove --toc if requested
        if getattr(args, "no_toc", False):
            makefile = (build_dir / "Makefile").read_text()
            makefile = makefile.replace("--toc \\\n", "").replace("--toc-depth=2 \\\n", "")
            (build_dir / "Makefile").write_text(makefile)

        # Build
        _run(["make", "source.pdf"], cwd=build_dir)

        # Determine output filename
        stem = getattr(args, "rename", None) or src.stem
        dest = src.parent / f"{stem}.pdf"
        shutil.copy2(build_dir / "source.pdf", dest)

    # Verify
    result = subprocess.run(
        ["pdfinfo", str(dest)], capture_output=True, text=True
    )
    size_line = next((l for l in result.stdout.splitlines() if "Page size" in l), "")
    print(f"\n  → {dest} ({dest.stat().st_size // 1024}KB)")
    if size_line:
        print(f"     {size_line.strip()}")

    return dest


# ── upload ────────────────────────────────────────────────────────────────────

def _upload(pdf: pathlib.Path, dest: str) -> None:
    _require("rclone")
    print(f"\n  Uploading → {dest}")
    _run(["rclone", "copy", str(pdf), dest, "--progress"])


# ── roundtrip ─────────────────────────────────────────────────────────────────

def cmd_roundtrip(args: argparse.Namespace) -> None:
    """Extract from PDF, then build RM2 PDF."""
    print(f"[1/2] Extracting {args.pdf}")
    outdir = cmd_extract(args)

    text_md = outdir / "text.md"
    if not text_md.exists():
        sys.exit(f"error: extraction produced no text.md in {outdir}")

    print(f"\n[2/2] Building {text_md}")
    # Merge build args: use the same rename/toc settings
    build_ns = argparse.Namespace(
        md=str(text_md),
        no_toc=getattr(args, "no_toc", False),
        rename=getattr(args, "rename", None),
    )
    pdf = cmd_build(build_ns)

    # Upload
    should_upload = getattr(args, "upload", False) and not getattr(args, "no_upload", False)
    if should_upload:
        gdrive = getattr(args, "gdrive", "gdrive:RM-Formatted/")
        _upload(pdf, gdrive)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # extract
    p_ex = sub.add_parser("extract", help="PDF → markdown + figures + tables")
    p_ex.add_argument("pdf", help="Input PDF")
    p_ex.add_argument("--out", help="Output directory")
    p_ex.add_argument("--key", help="OpenRouter API key")
    p_ex.add_argument("--dpi", type=int, default=200, help="Render DPI (default 200)")
    p_ex.add_argument("--thesis", action="store_true", help="Thesis mode (chapter headings)")

    # build
    p_bu = sub.add_parser("build", help="Markdown → RM2 PDF")
    p_bu.add_argument("md", help="Input Markdown file")
    p_bu.add_argument("--no-toc", dest="no_toc", action="store_true", help="Omit table of contents")
    p_bu.add_argument("--rename", help="Output PDF filename (without .pdf)")

    # roundtrip
    p_rt = sub.add_parser("roundtrip", help="PDF → extract → RM2 PDF")
    p_rt.add_argument("pdf", help="Input PDF")
    p_rt.add_argument("--out", help="Extraction output directory")
    p_rt.add_argument("--key", help="OpenRouter API key")
    p_rt.add_argument("--dpi", type=int, default=200, help="Render DPI (default 200)")
    p_rt.add_argument("--thesis", action="store_true", help="Thesis mode (chapter headings)")
    p_rt.add_argument("--no-toc", dest="no_toc", action="store_true", help="Omit table of contents")
    p_rt.add_argument("--rename", help="Output PDF filename (without .pdf)")
    p_rt.add_argument("--upload", action="store_true", help="Upload to GDrive via rclone")
    p_rt.add_argument("--no-upload", dest="no_upload", action="store_true", help="Skip GDrive upload")
    p_rt.add_argument("--gdrive", default="gdrive:RM-Formatted/", help="rclone GDrive destination")

    args = parser.parse_args()

    if args.cmd == "extract":
        cmd_extract(args)
    elif args.cmd == "build":
        cmd_build(args)
    elif args.cmd == "roundtrip":
        cmd_roundtrip(args)


if __name__ == "__main__":
    main()
