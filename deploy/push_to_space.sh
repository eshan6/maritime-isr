#!/usr/bin/env bash
# Push the Maritime ISR demo API to a Hugging Face Docker Space.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# `data/` is in .gitignore, so the corpus is NOT in the GitHub repo -- it is
# regenerated locally. The Space, though, needs it: the API serves a frozen
# snapshot and nothing in the container regenerates anything. So this assembles
# a Space repo out of two sources that no single `git push` can cover: the code
# from here, and the corpus from your local data/ directory.
#
# It is safe to re-run. It replaces the Space's contents each time rather than
# trying to merge, because a half-updated corpus is worse than a replaced one.
#
# USAGE
#   bash deploy/push_to_space.sh <your-hf-username>/<your-space-name>
#
# Example:
#   bash deploy/push_to_space.sh eshan6/maritime-isr-api

set -euo pipefail

SPACE="${1:-}"
if [ -z "$SPACE" ]; then
    echo "ERROR: give me the Space to push to."
    echo "  bash deploy/push_to_space.sh <hf-username>/<space-name>"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---- checks, before anything is copied or pushed -------------------------

fail() { echo "ERROR: $1" >&2; exit 1; }

command -v git >/dev/null || fail "git is not installed."
git lfs version >/dev/null 2>&1 || fail \
    "git-lfs is not installed, and the corpus needs it (files over 10 MB).
   Install it with:  sudo apt install git-lfs   (or: brew install git-lfs)
   then run:         git lfs install"

[ -d data/conformed ] || fail \
    "data/conformed is missing -- there is no corpus to ship.
   Generate it first, IN THIS ORDER (see deploy/README.md -- the order is
   load-bearing, graph-populate must come AFTER build-tracks or the graph
   never sees the encounters and the alert queue comes out empty):
       maritime-isr scenario generate
       maritime-isr build-tracks
       maritime-isr radar correlate --write
       maritime-isr baselines derive
       maritime-isr graph-populate"

[ -f frontend/dist/index.html ] || fail \
    "frontend/dist is missing -- the built UI is not there.
   Build it first:   cd frontend && npm install && npm run build"

DATA_MB=$(du -sm data | cut -f1)
echo "corpus size: ${DATA_MB} MB"
[ "$DATA_MB" -lt 2000 ] || fail \
    "data/ is ${DATA_MB} MB, which is too big to push comfortably.
   Something has accumulated that should not be shipped -- check data/raw
   and data/synthetic_scenes before continuing."

# ---- assemble ------------------------------------------------------------

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "staging in $WORK"

echo "cloning https://huggingface.co/spaces/$SPACE ..."
git clone "https://huggingface.co/spaces/$SPACE" "$WORK/space" 2>/dev/null || fail \
    "could not clone the Space.
   Create it first at https://huggingface.co/new-space
     - Space SDK must be **Docker**
     - Visibility: whatever you want, but see the note about it being public
   Then log in locally:  pip install huggingface_hub && huggingface-cli login"

cd "$WORK/space"

# Replace, do not merge. Keep .git (the remote) and nothing else.
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

cp "$REPO_ROOT/deploy/huggingface/Dockerfile" ./Dockerfile
cp "$REPO_ROOT/deploy/huggingface/README.md"  ./README.md
cp "$REPO_ROOT/pyproject.toml" ./

mkdir -p maritime_isr frontend
cp -r "$REPO_ROOT/maritime_isr/." ./maritime_isr/
cp -r "$REPO_ROOT/frontend/dist" ./frontend/dist
cp -r "$REPO_ROOT/data" ./data

# Nothing compiled or cached belongs in the image.
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
find . -name '*.pyc' -delete 2>/dev/null || true

# ---- large files need LFS or the push is rejected ------------------------

git lfs install --local
cat > .gitattributes <<'ATTRS'
*.parquet  filter=lfs diff=lfs merge=lfs -text
*.sqlite   filter=lfs diff=lfs merge=lfs -text
*.tif      filter=lfs diff=lfs merge=lfs -text
*.npy      filter=lfs diff=lfs merge=lfs -text
*.pkl      filter=lfs diff=lfs merge=lfs -text
*.pdf      filter=lfs diff=lfs merge=lfs -text
*.docx     filter=lfs diff=lfs merge=lfs -text
*.xlsx     filter=lfs diff=lfs merge=lfs -text
ATTRS

git add -A
git commit -q -m "Deploy Maritime ISR demo API (synthetic corpus snapshot)" || {
    echo "nothing changed since the last push -- the Space is already current."
    exit 0
}

echo "pushing (${DATA_MB} MB of corpus over LFS -- this is the slow part)..."
git push

cat <<DONE

================================================================
Pushed to https://huggingface.co/spaces/$SPACE

WHAT HAPPENS NOW
  Hugging Face builds the image. Watch the "Logs" tab on the Space
  page. First build takes several minutes; it installs the Python
  dependencies from scratch.

SUCCESS looks like
  The Space page shows "Running", and
    https://$(echo "$SPACE" | tr '/' '-' | tr '[:upper:]' '[:lower:]').hf.space/api/health
  returns {"status":"ok",...} in a browser.

FAILURE looks like
  "Build failed" in the Logs tab. The usual cause is a dependency
  with no prebuilt wheel; the log names it. Copy the last 20 lines
  back to Claude.

  If it builds but the page is blank, the app bound the wrong port
  -- check MISR_API_HOST is 0.0.0.0 in the Dockerfile.

NEXT
  Put that .hf.space URL into frontend/vercel.json, replacing
  REPLACE-ME, then deploy the frontend to Vercel.
================================================================
DONE
