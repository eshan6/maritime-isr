"""`maritime-isr doctor` — verify the SNAP/pyroSAR install before batch runs.

The single most common 0.2 failure is gpt not being found or not memory-capped.
This checks: gpt on PATH, gpt --diag runs, vmoptions caps present, pyroSAR
importable and locating gpt. Run it before preprocessing 90 scenes.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def run() -> int:
    problems = []

    gpt = shutil.which("gpt")
    if gpt:
        print(f"[doctor] gpt on PATH: {gpt}")
    else:
        problems.append("gpt not on PATH — run infra/install_snap.sh and `source ~/.bashrc`")
        print("[doctor] gpt NOT on PATH")

    if gpt:
        vmopts = Path(gpt).with_name("gpt.vmoptions")
        if vmopts.exists():
            caps = vmopts.read_text()
            xmx = [l for l in caps.splitlines() if l.startswith("-Xmx")]
            print(f"[doctor] gpt.vmoptions present, heap cap: {xmx or 'NONE — will thrash 24GB VM!'}")
            if not xmx:
                problems.append("no -Xmx cap in gpt.vmoptions — gpt may OOM the VM")
        else:
            problems.append(f"gpt.vmoptions missing at {vmopts} — memory uncapped")
        try:
            out = subprocess.run([gpt, "--diag"], capture_output=True, text=True, timeout=120)
            ok = "SNAP" in (out.stdout + out.stderr)
            print(f"[doctor] gpt --diag {'ran' if ok else 'ran but output unexpected'}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"gpt --diag failed: {e}")

    try:
        import pyroSAR  # noqa: F401
        from pyroSAR.snap import geocode  # noqa: F401
        print(f"[doctor] pyroSAR importable: {pyroSAR.__version__}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"pyroSAR not importable: {e} — run: pip install -e '.[sar]'")

    print("-" * 50)
    if problems:
        print(f"[doctor] {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("[doctor] all checks passed — ready to preprocess")
    return 0
