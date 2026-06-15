#!/usr/bin/env bash
# Build the arXiv-ready LaTeX source + PDF from the Markdown source.
# Requires: pandoc + tectonic  (brew install pandoc tectonic)
#
# Two-step on purpose: pandoc unconditionally loads \usepackage{xcolor} in its
# template *before* any header-include, so we cannot pass the `table` option
# (needed for alternating row shading on the multi-line tables) via the header.
# Instead we emit standalone paper.tex, inject the option ahead of pandoc's
# xcolor load, then compile. paper.tex doubles as the arXiv source.
set -euo pipefail
cd "$(dirname "$0")"

pandoc paper.md \
  -s -o paper.tex \
  -H arxiv-header.tex \
  -V geometry:margin=1in \
  -V fontsize=10pt \
  -V colorlinks=true \
  -V linkcolor=blue \
  -V urlcolor=blue

# Enable xcolor's table features (alternating row shading) by passing the
# option before pandoc's own \usepackage{xcolor}.
sed -i '' \
  's/^% Options for packages loaded elsewhere/% Options for packages loaded elsewhere\n\\PassOptionsToPackage{table}{xcolor}/' \
  paper.tex

tectonic paper.tex >/dev/null 2>&1

echo "wrote paper.tex + paper.pdf ($(pdfinfo paper.pdf 2>/dev/null | awk '/Pages/{print $2" pages"}'))"
