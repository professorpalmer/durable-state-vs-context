#!/usr/bin/env bash
# Build the arXiv-ready PDF from the Markdown source.
# Requires: pandoc + tectonic  (brew install pandoc tectonic)
set -euo pipefail
cd "$(dirname "$0")"

pandoc paper.md \
  -o paper.pdf \
  --pdf-engine=tectonic \
  -H arxiv-header.tex \
  -V geometry:margin=1in \
  -V fontsize=10pt \
  -V colorlinks=true \
  -V linkcolor=blue \
  -V urlcolor=blue

echo "wrote paper.pdf ($(pdfinfo paper.pdf 2>/dev/null | awk '/Pages/{print $2" pages"}'))"
