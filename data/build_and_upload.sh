#!/usr/bin/env bash
# Assemble the Hugging Face dataset from committed results and upload it.
#
#   1. Authenticate once:  hf auth login   (paste a WRITE token from
#      https://huggingface.co/settings/tokens), OR export HF_TOKEN=hf_xxx
#   2. Run:  bash data/build_and_upload.sh [hf-username]
#
# Creates/updates the dataset repo <user>/durable-vs-context-trials.
set -euo pipefail
cd "$(dirname "$0")/.."

USER="${1:-professorpalmer}"
REPO="$USER/durable-vs-context-trials"
STAGE="$(mktemp -d)"

cp results/canonical.jsonl                     "$STAGE/trials.jsonl"
cp results/sweep_aggregate.json                "$STAGE/concurrency_sweep_aggregate.json"
mkdir -p "$STAGE/concurrency_sweep_profiles"
cp results/profiles/sweep/c*_r*.jsonl          "$STAGE/concurrency_sweep_profiles/"
# Second-backend (Claude Code) concurrency probe — proves the cap is platform-specific.
cp results/claude_concurrency_aggregate.json   "$STAGE/claude_concurrency_aggregate.json"
mkdir -p "$STAGE/claude_concurrency_profiles"
cp results/profiles/claude_concurrency/c*_r*.json "$STAGE/claude_concurrency_profiles/"
cp data/DATASET_CARD.md                        "$STAGE/README.md"

echo "Uploading $(ls "$STAGE" | wc -l | tr -d ' ') files to dataset $REPO ..."
hf upload "$REPO" "$STAGE" --repo-type=dataset --commit-message "trials + replicated Cursor sweep + Claude second-backend probe"
echo "Done: https://huggingface.co/datasets/$REPO"
echo "Then add this URL to the GitHub repo README and the HF paper page."
