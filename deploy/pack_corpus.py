"""Pack the local corpus into one tarball to publish as a GitHub Release asset.

Python rather than shell because the operator is on Windows PowerShell, where
`bash deploy/pack_corpus.sh` needs Git Bash and a path translation that is one
more thing to get wrong. `python deploy/pack_corpus.py` is the same command on
every platform, and Python is already required to run the project at all.

`data/` is gitignored, so the deploy cannot get the corpus from the repo. This
makes the file the deploy downloads instead.

WHAT IS DELIBERATELY LEFT OUT
  data/synthetic_scenes  — SAR imagery, ~44 MB, read only by the local
                           preprocessing path. The API never opens it.
  __pycache__, *.pyc     — never useful anywhere.

USAGE
  python deploy/pack_corpus.py
"""
from __future__ import annotations

import sys
import tarfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "maritime-isr-corpus.tar.gz"

EXCLUDE_DIRS = {"synthetic_scenes", "__pycache__"}

REPO = "eshan6/maritime-isr"

ORDER_HINT = """   Generate it first, IN THIS ORDER (graph-populate must be LAST,
   because it reads what the earlier steps landed):

       maritime-isr scenario generate
       maritime-isr build-tracks
       maritime-isr radar correlate --write
       maritime-isr baselines derive
       maritime-isr graph-populate
"""


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def _keep(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    return path.suffix != ".pyc"


def main() -> int:
    if not (DATA / "conformed").is_dir():
        return fail("data/conformed is missing — there is no corpus to pack.\n"
                    + ORDER_HINT)
    if not (DATA / "graph.sqlite").exists():
        return fail("data/graph.sqlite is missing — the graph was never built.\n"
                    "   Run:  maritime-isr graph-populate")

    print("packing (excluding synthetic_scenes — the API never reads it)...")
    n = 0
    with tarfile.open(OUT, "w:gz") as tar:
        for path in sorted(DATA.rglob("*")):
            rel = path.relative_to(DATA)
            if not _keep(rel):
                continue
            if path.is_file():
                tar.add(path, arcname=str(rel).replace("\\", "/"))
                n += 1

    size_mb = OUT.stat().st_size / (1024 * 1024)
    tag = f"corpus-{date.today():%Y%m%d}"

    print(f"""
================================================================
Packed {n:,} file(s) into:
  {OUT}
  {size_mb:.0f} MB

NEXT
  1. Open  https://github.com/{REPO}/releases/new
  2. Tag: {tag}          Title: anything
  3. Drag the file above into the "Attach binaries" box.
     WAIT for the upload bar to finish.
  4. Click "Publish release".
  5. RIGHT-CLICK the uploaded file name -> Copy Link Address.
     It must look like:
       https://github.com/{REPO}/releases/download/{tag}/{OUT.name}
     If your link has "/blob/" or no "/download/" in it, you copied
     the page address instead of the file. Go back to step 5.
  6. Paste that link into MISR_CORPUS_URL in the Render dashboard,
     then click Manual Deploy.

SUCCESS looks like
  The Render build log prints "downloaded {size_mb:.0f} MB, extracting"
  and then "corpus ready at ...".

FAILURE looks like
  "MISR_CORPUS_URL is not set"  -> step 6 was skipped.
  A 404 during the download     -> the release is still a draft, or
                                   the link came from step 5's warning.
================================================================""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
