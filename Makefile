# reCompose Makefile
# Converts .md files to reMarkable 2-optimized PDFs via Pandoc + XeLaTeX
#
# Usage:
#   make                     # build all .md → .pdf
#   make report.pdf          # build specific file
#   make clean               # remove all built PDFs and intermediate .tex
#   make rebuild             # clean + build all
#   make upload              # upload PDFs via rclone (set GDRIVE_DEST below)

PANDOC       := pandoc
TEMPLATE     := rm2.latex
PDF_ENGINE   := xelatex
GDRIVE_DEST  := gdrive:RM-Formatted/   # adjust to your rclone remote
PANDOC_FLAGS := --pdf-engine=$(PDF_ENGINE) \
                --template=$(TEMPLATE) \
                --from=markdown+smart \
                --standalone \
                --toc \
                --toc-depth=2 \
                -V colorlinks=true \
                -V documentclass=article

# All markdown files in the current directory
SRC := $(wildcard *.md)
PDF := $(SRC:.md=.pdf)
TEX := $(SRC:.md=.tex)

# ── Targets ───────────────────────────────────────────────────────

.PHONY: all clean rebuild list upload

all: $(PDF)

# Three-pass build: .md → .tex (pandoc) → .tex (fix_tables.py) → .pdf (xelatex)
%.pdf: %.md $(TEMPLATE) fix_tables.py
	@echo "  [1/3] Pandoc:  $< → $*.tex"
	@$(PANDOC) $(PANDOC_FLAGS) -o $*.tex $<
	@echo "  [2/3] Tables:  fix_tables.py (longtable → xltabular)"
	@python3 fix_tables.py $*.tex
	@echo "  [2.5/3] Bibliography spacing"
	@sed -i 's/\\hypertarget{bibliography}{%/\\begingroup\\setlength{\\parskip}{4pt plus 2pt minus 1pt}\\setstretch{1.25}\\hypertarget{bibliography}{%/' $*.tex
	@grep -q '\\begingroup' $*.tex && sed -i 's/^\\end{document}/\\endgroup\n\\end{document}/' $*.tex || true
	@echo "  [3/3] XeLaTeX: $*.tex → $@"
	@$(PDF_ENGINE) -interaction=nonstopmode -halt-on-error $*.tex > /dev/null 2>&1
	@rm -f $*.aux $*.log $*.out $*.toc
	@echo "  ✓ $@ built ($$(du -h $@ | cut -f1))"

clean:
	rm -f $(PDF) $(TEX) *.aux *.log *.out *.toc
	@echo "Cleaned all build artifacts."

rebuild: clean all

upload: $(PDF)
	@for f in $(PDF); do \
		echo "  Uploading $$f → $(GDRIVE_DEST)"; \
		rclone copy "$$f" "$(GDRIVE_DEST)" && \
		echo "  ✓ $$f uploaded" || echo "  ✗ $$f upload failed"; \
	done

list:
	@echo "Sources: $(SRC)"
	@echo "PDFs:    $(PDF)"