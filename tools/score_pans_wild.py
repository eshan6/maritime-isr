"""Score the extractor against the wild fixtures in `tests/pans_wild.py`.

Run from the repo root:  ``python tools/score_pans_wild.py``

The number this prints is **on the synthetic suite** and on a *harder* synthetic
than the corpus — labels and notations the generator never writes. It says
nothing about real agency mail, which has not been seen yet.
"""
from __future__ import annotations

import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maritime_isr.ingest.pans.extract import (extract_notification,  # noqa: E402
                                              parse_eta)
from maritime_isr.ingest.pans.readers import Passage  # noqa: E402
from tests.pans_wild import (WILD_ABSENCES, WILD_DATES,  # noqa: E402
                             score_documents)


def main() -> None:
    r = score_documents(extract_notification, Passage)
    print(f"documents      : {r['expected']} expected field(s)")
    print(f"  correct      : {r['correct']}  ({r['accuracy']:.1%})")
    print(f"  missed       : {r['missed']}")
    print(f"  wrong value  : {r['wrong_value']}")
    print(f"  MISATTRIBUTED: {r['misattributed']}")
    for m in r["misattributions"]:
        print(f"      {m}")
    for f in r["failures"]:
        print(f"      {f}")

    ok = 0
    for text, want in WILD_DATES:
        got = parse_eta(text)
        got = got.astimezone(timezone.utc).isoformat() if got else None
        if got == want:
            ok += 1
        else:
            print(f"      DATE {text!r} -> {got}, want {want}")
    print(f"dates          : {ok}/{len(WILD_DATES)}")

    absent = 0
    for text in WILD_ABSENCES:
        fields = extract_notification(
            [Passage(f"Cargo: {text}", "page 1", 0.97, "pdf_text")])
        f = fields.get("cargo")
        if f is not None and f.value is None:
            absent += 1
        else:
            print(f"      ABSENCE {text!r} -> "
                  f"{None if f is None else f.value!r}")
    print(f"absences       : {absent}/{len(WILD_ABSENCES)}")


if __name__ == "__main__":
    main()
