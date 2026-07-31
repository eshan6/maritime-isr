"""Read the real OFAC snapshot out of DuckDB.

Split into its own module so the scenario collision guard and the
corpus profiler share one implementation, and so neither has to
import the matcher wholesale.
"""
from __future__ import annotations


def ofac_imos_from_duckdb() -> set[str]:
    """Every IMO in the real OFAC snapshot, read from DuckDB.

    **OFAC does not live in a conformed table.** `registries.py` writes it to
    DuckDB as `ofac_sdn`, and there is no `sanctions` parquet table at all —
    confirmed against the operator's corpus, where that directory does not
    exist. Reading `read_table("sanctions")` therefore returned nothing and the
    collision guard silently checked against a far smaller denominator than it
    claimed to.

    OFAC has no IMO column; the number is regexed out of the free-text
    `remarks` field, which is the same extraction `sanctions_match` performs.
    Reusing `normalise_imo` here means the guard sees exactly the hulls the
    matcher would.
    """
    try:
        from ..db import connect
        from .sanctions_match import normalise_imo
    except Exception:                                       # pragma: no cover
        return set()
    out: set[str] = set()
    try:
        con = connect(read_only=True)
    except Exception:                                       # noqa: BLE001
        return set()
    try:
        rows = con.execute(
            "SELECT remarks FROM ofac_sdn "
            "WHERE lower(coalesce(sdn_type,'')) = 'vessel'").fetchall()
    except Exception:                                       # noqa: BLE001
        return set()                    # table absent on this machine
    finally:
        try:
            con.close()
        except Exception:                                   # noqa: BLE001
            pass
    import re
    for (remarks,) in rows:
        for m in re.findall(r"\b(\d{7})\b", str(remarks or "")):
            v = normalise_imo(m)
            if v:
                out.add(str(v))
    return out
