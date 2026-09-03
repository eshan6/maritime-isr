#!/usr/bin/env bash
# Pack the local corpus into one tarball to publish as a GitHub Release asset.
#
# `data/` is gitignored, so the deploy cannot get the corpus from the repo. This
# makes the file the deploy downloads instead.
#
# WHAT IS DELIBERATELY LEFT OUT
#   data/synthetic_scenes  — SAR imagery, ~44 MB, only read by the local
#                            preprocessing path. The API never opens it.
#   __pycache__, *.pyc     — never useful anywhere.
#
# USAGE
#   bash deploy/pack_corpus.sh
#
# Then upload the file it prints to a GitHub Release and put that asset's URL
# into MISR_CORPUS_URL on Render. See deploy/README.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail() { echo "ERROR: $1" >&2; exit 1; }

[ -d data/conformed ] || fail \
    "data/conformed is missing — there is no corpus to pack.
   Generate it first, IN THIS ORDER (graph-populate must be LAST):
       maritime-isr scenario generate
       maritime-isr build-tracks
       maritime-isr radar correlate --write
       maritime-isr baselines derive
       maritime-isr graph-populate"

[ -f data/graph.sqlite ] || fail \
    "data/graph.sqlite is missing — the graph was never built.
   Run:  maritime-isr graph-populate"

OUT="maritime-isr-corpus.tar.gz"

echo "packing (excluding synthetic_scenes — the API never reads it)..."
tar -czf "$OUT" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='synthetic_scenes' \
    -C data .

SIZE_MB=$(du -m "$OUT" | cut -f1)

cat <<DONE

================================================================
Packed: $REPO_ROOT/$OUT  (${SIZE_MB} MB)

NEXT
  1. Go to https://github.com/eshan6/maritime-isr/releases/new
  2. Tag: corpus-$(date +%Y%m%d)      Title: anything
  3. Drag $OUT into the "Attach binaries" box
  4. Publish, then RIGHT-CLICK the uploaded file and copy its link.
     It looks like:
       https://github.com/eshan6/maritime-isr/releases/download/corpus-$(date +%Y%m%d)/$OUT
  5. Put that link into MISR_CORPUS_URL in the Render dashboard,
     then trigger a redeploy.

SUCCESS looks like
  The Render build log prints "downloaded ${SIZE_MB} MB, extracting"
  and then "corpus ready at ...".

FAILURE looks like
  "MISR_CORPUS_URL is not set" — step 5 was skipped.
  A 404 during download — the release is a draft, or the link was
  copied from the release page rather than the asset itself.
================================================================
DONE
