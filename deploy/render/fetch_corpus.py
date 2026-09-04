"""Fetch the synthetic corpus at build time, because it is not in the repo.

`data/` is gitignored — the corpus is generated locally and regenerating it on
a 0.1-CPU free instance is not on the table. So the deploy pulls a tarball that
was built on the laptop and published as a GitHub Release asset: free, no LFS,
up to 2 GB per file, and public, which this data is anyway.

Run from the repo root as a build step. It is a no-op when the corpus is
already present, so a redeploy that reuses the build cache does not re-download
a quarter of a gigabyte.

`MISR_CORPUS_URL` points at the asset. It is deliberately an env var rather
than a constant: the corpus is rebuilt whenever the scenario changes, and the
URL carries a release tag, so hardcoding it would silently pin the deploy to
whichever corpus happened to exist the day this file was written.
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

#: The corpus is useless without these. Their presence is what "already
#: fetched" means — an empty `data/` directory left by a previous failed run
#: must not read as success.
REQUIRED = ("conformed", "graph.sqlite")


def _already_here() -> bool:
    return all((DATA / name).exists() for name in REQUIRED)


def main() -> int:
    if _already_here():
        print(f"corpus already present at {DATA} — nothing to fetch")
        return 0

    url = os.getenv("MISR_CORPUS_URL", "").strip()
    if not url:
        print(
            "ERROR: MISR_CORPUS_URL is not set, so there is no corpus to serve.\n"
            "\n"
            "  Set it in the Render dashboard (Environment -> Add Environment\n"
            "  Variable) to the GitHub Release asset URL for the corpus\n"
            "  tarball. deploy/README.md has the steps for building and\n"
            "  uploading it.\n"
            "\n"
            "  Failing the build here on purpose: a service that starts with no\n"
            "  corpus comes up green and serves empty lists, which looks like a\n"
            "  broken product rather than a missing file.",
            file=sys.stderr,
        )
        return 1

    print(f"fetching corpus from {url}")
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(url, timeout=300) as resp, \
                open(tmp_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        size_mb = tmp_path.stat().st_size / (1024 * 1024)
        print(f"downloaded {size_mb:.0f} MB, extracting")

        with tarfile.open(tmp_path, "r:gz") as tar:
            # The tarball is built by us from our own corpus, but extraction is
            # still bounded: a path escaping the data directory would write
            # somewhere in the deploy that nothing here should touch.
            safe = []
            for member in tar.getmembers():
                target = (DATA / member.name).resolve()
                if not str(target).startswith(str(DATA.resolve())):
                    print(f"  refusing path outside data/: {member.name}",
                          file=sys.stderr)
                    return 1
                safe.append(member)
            tar.extractall(DATA, members=safe)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not _already_here():
        missing = [n for n in REQUIRED if not (DATA / n).exists()]
        print(f"ERROR: extracted, but {missing} are missing — the tarball is "
              f"not a corpus built from data/. Rebuild it with "
              f"deploy/pack_corpus.py.", file=sys.stderr)
        return 1

    print(f"corpus ready at {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
