"""Throwaway probe: honest-hull false-accusation rate and the authored lies.

Deliberately ugly and zero-polish (CLAUDE.md §6 — inspection views are
throwaway). It answers the one question the mismatch rule has to answer before
anything is wired to a corpus: on a fleet of hulls that all declare what they
are, how often does a single look accuse one of them, and do the two lies the
scenarios author actually fire?
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.anomaly.imagery import check_declared_type  # noqa: E402
from maritime_isr.eo.appearance import descriptor_for, observe  # noqa: E402
from maritime_isr.eo.classify import (PROTOTYPE_HULLS,  # noqa: E402
                                      PrototypeClassifier,
                                      ReferenceLibrary,
                                      SilhouetteClassifier, separability_at)
from maritime_isr.eo.conditions import BAND_THERMAL, BAND_VISIBLE  # noqa: E402


def honest(clf, band, q, n=200):
    lib, rng = ReferenceLibrary(), random.Random(3)
    tot = cross = ok = nc = 0
    for cls, (length, beam, dr) in PROTOTYPE_HULLS.items():
        proto = descriptor_for(cls, length_m=length, beam_m=beam, draught_m=dr)
        for _ in range(n):
            seen = observe(proto, aspect_deg=80.0, quality=q, band=band,
                           rng=rng)
            v = clf.classify(seen, quality=q, band=band, library=lib)
            f = check_declared_type(declared_class=cls, verdict=v, quality=q,
                                    band=band)
            tot += 1
            cross += f.outcome == "contradiction"
            ok += f.outcome == "ok"
            nc += f.outcome == "not_checkable"
    return tot, cross, ok, nc


def lie(clf, band, q, physical, declared, n=200):
    lib, rng = ReferenceLibrary(), random.Random(9)
    length, beam, dr = PROTOTYPE_HULLS[physical]
    proto = descriptor_for(physical, length_m=length, beam_m=beam, draught_m=dr)
    hit = nc = ok = 0
    for _ in range(n):
        seen = observe(proto, aspect_deg=80.0, quality=q, band=band, rng=rng)
        v = clf.classify(seen, quality=q, band=band, library=lib)
        f = check_declared_type(declared_class=declared, verdict=v, quality=q,
                                band=band)
        hit += f.outcome == "contradiction"
        ok += f.outcome == "ok"
        nc += f.outcome == "not_checkable"
    return hit, ok, nc


def main() -> int:
    for clf in (PrototypeClassifier(), SilhouetteClassifier()):
        print("=" * 72)
        print(clf.name)
        for band in (BAND_VISIBLE, BAND_THERMAL):
            for q in (0.5, 0.65, 0.8):
                s = separability_at(q, band, model=clf.name,
                                    restrict=clf._restrict)
                tot, cross, ok, nc = honest(clf, band, q)
                print(f"  {band:8s} q={q}  T={s['temperature']:.2f} "
                      f"acc={s['calibration']['accuracy']:.2f}  "
                      f"vocab={s['vocabulary']}")
                print(f"           honest n={tot} FALSE={cross} "
                      f"({cross / tot:.2%}) ok={ok} nc={nc}")
                for physical, declared, tag in (
                        ("Suezmax", "fishing", "O1 tanker says trawler"),
                        ("general_cargo", "product_tanker", "O2 crane ship"),
                        ("general_cargo", "bulker", "O3 DECOY same family"),
                ):
                    hit, o, n = lie(clf, band, q, physical, declared)
                    print(f"           {tag:34s} fire={hit:3d} ok={o:3d} "
                          f"nc={n:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
