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

cp results/canonical.jsonl                 "$STAGE/trials.jsonl"
cp results/profiles/ksweep_c8.jsonl        "$STAGE/concurrency_probe_c8.jsonl"
cp results/profiles/ksweep_c12.jsonl       "$STAGE/concurrency_probe_c12.jsonl"
cp results/profiles/probe_c16_v2.jsonl     "$STAGE/concurrency_probe_c16.jsonl"
cp results/profiles/ksweep_c24.jsonl       "$STAGE/concurrency_probe_c24.jsonl"
cp results/profiles/sweep_c32_clean.jsonl  "$STAGE/concurrency_probe_c32.jsonl"
cp results/ksweep_summary.json             "$STAGE/ksweep_summary.json"
cp data/DATASET_CARD.md                    "$STAGE/README.md"

echo "Uploading $(ls "$STAGE" | wc -l | tr -d ' ') files to dataset $REPO ..."
hf upload "$REPO" "$STAGE" --repo-type=dataset --commit-message "trial records + 5-point concurrency sweep"
echo "Done: https://huggingface.co/datasets/$REPO"
echo "Then add this URL to the GitHub repo README and the HF paper page."
