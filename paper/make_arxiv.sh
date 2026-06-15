#!/usr/bin/env bash
# Assemble a SELF-CONTAINED arXiv submission from paper.tex + the embedded figures.
#
# paper.tex (pandoc -s) inlines arxiv-header.tex into its preamble, so the only external
# dependency is the figure PNGs, referenced as ../figures/NAME.png. arXiv compiles in a
# flat tree, so we copy the used figures into a figures/ subdir and rewrite the paths.
#
# Output: arxiv/ (uploadable dir) and arxiv-submission.tar.gz (uploadable tarball).
# Verifies the package compiles standalone with tectonic before tarring.
set -euo pipefail
cd "$(dirname "$0")"

FIGS=(monolith_scaling resumability headroom_vs_scale concurrency_ceiling concurrency_backends)

rm -rf arxiv arxiv-submission.tar.gz
mkdir -p arxiv/figures
for f in "${FIGS[@]}"; do
  cp "../figures/$f.png" "arxiv/figures/$f.png"
done

# Rewrite ../figures/ -> figures/ so the tree is flat and self-contained.
sed 's#\.\./figures/#figures/#g' paper.tex > arxiv/paper.tex

echo "Verifying self-contained compile (tectonic)..."
( cd arxiv && tectonic paper.tex >/dev/null 2>&1 )
echo "  OK: $(pdfinfo arxiv/paper.pdf 2>/dev/null | awk '/Pages/{print $2" pages"}')"
rm -f arxiv/paper.pdf  # arXiv builds its own; keep only sources + figures

tar czf arxiv-submission.tar.gz -C arxiv .
echo "wrote arxiv/ and arxiv-submission.tar.gz"
echo "Upload arxiv-submission.tar.gz to arXiv (it contains paper.tex + figures/)."
