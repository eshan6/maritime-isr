"""The one place a vessel or identity node id is constructed. ADR-022.

**Why this file exists.** The graph populator and the identity resolver each
built node ids with their own f-string, and they had never met. Measured on the
synthetic corpus 2026-08-01:

  * `from_landed` created **114** hull nodes as `vessel:gfw:<vessel_id>`,
    carrying 616 edges between them — flag, owner, sanctions, port calls,
    encounters. All the structure.
  * `identity.resolve_mmsi` created **8** nodes as `vessel:mmsi:<mmsi>`,
    carrying 8 edges — one self-referential `identified-as` each. Nothing else.
  * **8 of 8** of those had a fully-populated twin under the other key.

Every alert the anomaly library raised landed on the second kind. The alert
*resolved* — it pointed at a real row in `nodes` — so a presence check passed.
It pointed at `{"mmsi": 999000012, "provisional": true}` and nothing else, while
the hull with the ownership chain sat one keyspace away. An analyst clicking
that alert reaches a stub.

**The root cause was narrower than the symptom.** `resolve_mmsi` works by
walking `identified-as` edges into an `id:mmsi:<mmsi>` node, which is the right
design. It fell through to minting a provisional hull because
`from_landed.add_identities` emitted **`id:name:*` nodes only** — 115 of them,
and zero `id:mmsi:*` or `id:imo:*`. The lookup had nothing to find. So the two
sides did not merely disagree on a format; one side never published the key the
other side reads.

**The fix is therefore structural rather than a translation shim.** A shim
mapping `vessel:mmsi:X` onto `vessel:gfw:Y` would have to be consulted by every
present and future consumer, and the first one that forgot would silently see
half a graph. Instead: one function mints hull ids, one function mints identity
ids, and the populator publishes the identity nodes the resolver reads. After
that the resolver finds the hull on its own and no second node is created.

**On the double prefix.** Real GFW vessel ids are bare (`d88dfa283-31bc-…`);
the scenario generator writes its own entity id (`vessel:spine`) into the same
column, so the prefix was applied twice and produced `vessel:gfw:vessel:spine`.
That meant the real and synthetic corpora did not even share a node-id *shape*.
`vessel_node_id` now strips a leading `vessel:` before namespacing, so both land
as `vessel:gfw:<native>`.
"""
from __future__ import annotations

__all__ = ["vessel_node_id", "identity_node_id", "native_vessel_id",
           "IDENTITY_KINDS", "VESSEL_PREFIX", "IDENTITY_PREFIX"]

VESSEL_PREFIX = "vessel"
IDENTITY_PREFIX = "id"

#: Every identity kind the graph records. Two tiers, and the distinction is
#: worth keeping in mind rather than encoding:
#:
#:   * **Keys** — `mmsi`, `imo`, `call_sign`. An MMSI or an IMO identifies a
#:     hull, so a lookup by one must be able to find it. These are what
#:     `resolve_mmsi` reads and what the populator failed to publish (ADR-022).
#:   * **Labels** — `name`, `flag`. Not unique: two ships may share a name and
#:     thousands share a flag, so one of these nodes can have many hulls
#:     pointing at it and no caller may treat a hit as identification.
#:
#: The list is closed on purpose. A new kind is a deliberate edit *here*, which
#: is what stops the resolver and the populator inventing spellings for each
#: other again — the exact drift ADR-022 exists to prevent.
IDENTITY_KINDS = ("mmsi", "imo", "call_sign", "name", "flag")


#: Namespaces this project mints. Only these are stripped, so a native id that
#: happens to contain a colon is never mangled.
KNOWN_SOURCES = ("gfw", "imo", "mmsi", "scenario")


def native_vessel_id(raw: str) -> str:
    """Strip any namespace this project already applied. **Idempotent.**

    The scenario generator writes `vessel:spine` into the `vessel_id` column
    that GFW fills with a bare id. Without this, namespacing produced
    `vessel:gfw:vessel:spine` on synthetic rows and `vessel:gfw:d88dfa…` on real
    ones, so the two corpora could not be compared on node id at all.

    **Idempotency is the property that matters**, and the first version did not
    have it: passing an already-canonical `vessel:gfw:spine` back in produced
    `vessel:gfw:gfw:spine`. That is not a hypothetical — a canonical id is
    exactly what a caller holds after one round trip through the graph, and the
    old double-prefix bug was itself an id being namespaced twice. Caught by the
    round-trip assertion in the test rather than by inspection.

    Only our own prefixes are removed. A source id that legitimately contains a
    colon keeps it.
    """
    s = str(raw).strip()
    while True:
        if s.startswith(f"{VESSEL_PREFIX}:"):
            s = s[len(VESSEL_PREFIX) + 1:]
            continue
        head = s.split(":", 1)[0]
        if head in KNOWN_SOURCES and ":" in s:
            s = s.split(":", 1)[1]
            continue
        return s


def vessel_node_id(raw: str, *, source: str = "gfw") -> str:
    """The canonical graph node id for a hull.

    `source` names the id space the native id came from, so two sources cannot
    collide on a shared integer. It is not a claim about the data's realness —
    that is `is_synthetic` on the node, and ADR-019 keeps the two separate on
    purpose.
    """
    return f"{VESSEL_PREFIX}:{source}:{native_vessel_id(raw)}"


def identity_node_id(kind: str, value) -> str:
    """The canonical graph node id for one identity assertion.

    **Both sides of the MMSI join must call this.** The populator writes
    `identified-as` edges pointing at these nodes; the resolver reads them to
    answer "which hull was broadcasting this MMSI at time t". They were
    previously constructed by two different f-strings in two modules, and the
    populator only ever emitted the `name` kind.
    """
    if kind not in IDENTITY_KINDS:
        raise ValueError(
            f"unknown identity kind {kind!r}; expected one of {IDENTITY_KINDS}. "
            "Adding a kind means adding it here, so the resolver and the "
            "populator cannot drift apart again.")
    return f"{IDENTITY_PREFIX}:{kind}:{_canonical_value(kind, value)}"


def _canonical_value(kind: str, value) -> str:
    """One spelling per identifier, whatever type the caller happened to hold.

    **The two sides of this join hold different types for the same number.**
    `gfw_vessel_identity.mmsi` lands as a *string*; `ais_position.mmsi` lands as
    an *int*; a Parquet round-trip can turn either into a float. `str(999000012)`
    and `str(999000012.0)` are different node ids, and the join would miss for a
    reason no row count would reveal — the same shape of defect as ADR-015's H3
    mismatch and as the shadow stub this key exists to prevent.

    Numeric kinds are therefore normalised through int. Anything that will not
    parse is passed through as trimmed text rather than dropped, because a
    malformed identifier is a fact about the source and losing it would be worse
    than carrying it.
    """
    if kind in ("mmsi", "imo"):
        try:
            return str(int(float(str(value).strip())))
        except (TypeError, ValueError):
            return str(value).strip()
    return str(value).strip()
