"""generate / clear / status — the three verbs, and the reporting discipline.

**`generate` reports what landed, not what it built.** The two differ: rows are
merged on a natural key, clipped at the window edge, and skipped when a vessel
has no AIS to emit. Four bugs of the reported-vs-landed family have already been
found in this codebase (STATE.md), so `generate` finishes by reading the tables
back off disk and printing those counts.

**`status` never prints a blended total.** Every count comes back split real
versus synthetic, because any number that could be quoted externally has to be
splittable (ADR-019). A single combined figure in a status output is exactly how
a scenario count ends up in a slide as a real one.

**`clear` deletes every `is_synthetic=TRUE` row and nothing else.** It rewrites
each partition without them rather than deleting files, so a partition holding
both real and scenario rows keeps its real ones.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import DATA_ROOT, GRAPH_DB_NAME
from ..ingest.landing import (conformed_dir, read_parquet_rows, read_table,
                              split_real_synthetic, table_day_partitions)
from .cast import build_cast, cohorts, expected_vessel_count
from .identifiers import assert_no_collisions, reserve_against_corpus
from .land import ALL_TABLES, land_world
from .profile import CorpusProfile
from .radar import format_report as format_radar, generate_radar_picture
from .scenarios import run_all
from .truth import TABLE as TRUTH_TABLE
from .validate import validate_world
from .world import ScenarioWorld


@dataclass
class GenerationResult:
    world: ScenarioWorld
    built: dict
    landed: dict
    validation: object
    ran: list

    @property
    def ok(self) -> bool:
        return self.validation.ok


def generate(seed: int = 7, *, land: bool = True, radar: bool = True,
             profile_path=None) -> GenerationResult:
    """Build the world, validate it, land it, and re-read what landed.

    **Clears the previous synthetic corpus first.** Landing merges on a natural
    key *within a day partition*, so a scenario whose timing shifted between
    runs lands twice — once under each day — and the truth table quietly grows a
    duplicate. Observed: DX12 and E7 appearing twice after a reschedule, which
    would have double-counted them in every subsequent measurement. Generate
    means "produce exactly the corpus for this seed", not "merge with whatever
    was there".
    """
    if land:
        clear()
    profile = CorpusProfile.load(profile_path)

    # Before a single hull is named, find out which reserved-band identifiers
    # the real corpus on THIS machine already uses, so the cast can be minted
    # around them. Done here rather than inside the mint functions because it
    # reads the landed tables once, and because the operator has to be told:
    # hull assignment depends on the corpus present at generation time.
    #
    # The profile is handed over so that the reservation and the collision
    # guard below consult the *same* set of real identifiers. Where there is no
    # landed corpus — every sandbox, and the operator's laptop before ingest —
    # only the profile knows which reserved-band numbers a real transmitter has
    # broadcast, and a guard that checks against a set it never reserved against
    # fails the build instead of preventing the clash.
    taken = reserve_against_corpus(profile=profile)
    if taken["imos"] or taken["mmsis"]:
        print(f"[scenario] {len(taken['imos'])} reserved IMO(s) and "
              f"{len(taken['mmsis'])} reserved MMSI(s) are already used by the "
              f"{taken['source']} — minting around them: "
              f"{taken['imos'][:5]}{taken['mmsis'][:5]}")

    world = ScenarioWorld.new(seed, profile)
    build_cast(world)
    ran = run_all(world)
    world.identity.close_window(world.t1)

    # The radar picture is generated LAST and from nothing but the tracks the
    # scenarios wrote (ADR-028). It has to be last: it walks every vessel's
    # completed motion and asks which station could have seen her, so a scenario
    # that had not run yet would be invisible to it. And it has to be derived
    # rather than authored, because that is what makes a radar-only contact and
    # an AIS-only track two views of one ship rather than two fabrications.
    if radar:
        world.radar = generate_radar_picture(world)

    # Identifier collisions are fatal before anything touches disk. Landing a
    # colliding hull and cleaning up afterwards is not equivalent: the row would
    # exist, briefly, in the same table as our findings.
    imos, mmsis, refs = world.all_identifiers()
    assert_no_collisions(imos, mmsis, refs, profile=profile,
                         raise_on_collision=True)

    built = world.counts()

    # ---- arrival notifications, written as documents (ADR-036) ----------
    #
    # **Written to disk as PDF, Word, spreadsheet and JSON, not landed as
    # rows.** They are *inputs*: the same standing an unread email attachment
    # has. The extractor reads the files, exactly as it would read a real
    # inbox, and lands what it managed to get out of them. Landing the specs
    # directly would produce a corpus in which extraction always succeeds
    # perfectly, which is the one thing Area 4 must not assume.
    if land and getattr(world, "pans_specs", None):
        from .pans import PANS_DIRNAME, write_notifications
        inbox = DATA_ROOT / PANS_DIRNAME
        built["pans_documents"] = sum(
            v for v in write_notifications(
                world.pans_specs, inbox, seed=seed).values()
            if isinstance(v, int))

    landed = land_world(world) if land else {}
    validation = validate_world(world)
    return GenerationResult(world, built, landed, validation, ran)


def landed_counts() -> dict:
    """Rows on disk per table, split real vs synthetic. Read back, not assumed."""
    out = {}
    for table in ALL_TABLES:
        try:
            rows = read_table(table)
        except Exception:                                   # noqa: BLE001
            continue
        real, syn = split_real_synthetic(rows)
        if real or syn:
            out[table] = dict(real=len(real), synthetic=len(syn))
    return out


def _unlink(path, *, attempts: int = 5) -> None:
    """Delete a file, tolerating a Windows scanner holding it for a moment.

    Even with our own handles released, an antivirus or the search indexer can
    hold a freshly written file briefly. That is transient, so a few short
    retries turn a hard failure into a pause. The last attempt is allowed to
    raise: a file that is still locked after a second is a real problem and
    should be reported rather than silently skipped, which would leave
    synthetic rows in a corpus the operator believes is clean.
    """
    for i in range(attempts):
        try:
            path.unlink()
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


def clear() -> dict:
    """Delete every synthetic row from every table. Returns rows removed.

    Partition files are rewritten without the synthetic rows rather than
    deleted, so a partition that holds both kinds keeps the real ones. The truth
    table is scenario-only and is removed entirely.
    """
    # The inbox first. A generated document left on disk would be re-extracted
    # by the next run into a corpus that no longer holds the vessel it names —
    # which would land as an unmatched-notification finding and be indisputably
    # our own fault.
    from .pans import PANS_DIRNAME
    inbox = DATA_ROOT / PANS_DIRNAME
    if inbox.exists():
        n_docs = sum(1 for p in inbox.iterdir() if p.is_file())
        shutil.rmtree(inbox, ignore_errors=True)
        if n_docs:
            removed_docs = n_docs
        else:
            removed_docs = 0
    else:
        removed_docs = 0

    removed: dict[str, int] = {}
    if removed_docs:
        removed["pans_inbox (documents)"] = removed_docs
    for table in ALL_TABLES:
        n = 0
        for path in table_day_partitions(table):
            try:
                rows = read_parquet_rows(path)
            except Exception:                               # noqa: BLE001
                continue
            keep = [r for r in rows if not r.get("is_synthetic")]
            n += len(rows) - len(keep)
            if len(keep) == len(rows):
                continue
            if keep:
                pq.write_table(pa.Table.from_pylist(keep), path,
                               compression="zstd")
            else:
                _unlink(path)
        if n:
            removed[table] = n
    # The truth table has no real rows by construction.
    tdir = conformed_dir(TRUTH_TABLE)
    if tdir.exists():
        shutil.rmtree(tdir, ignore_errors=True)

    # Synthetic graph rows go too, so a re-run does not accumulate.
    db = DATA_ROOT / GRAPH_DB_NAME
    if db.exists():
        import sqlite3
        con = sqlite3.connect(str(db))
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(edges)")}
            if "is_synthetic" in cols:
                for tbl in ("edges", "alerts", "nodes"):
                    cur = con.execute(
                        f"DELETE FROM {tbl} WHERE is_synthetic=1")
                    if cur.rowcount > 0:
                        removed[f"graph.{tbl}"] = cur.rowcount
                con.commit()
        finally:
            con.close()
    return removed


def status() -> dict:
    """Counts by table and by scenario, always split."""
    tables = landed_counts()
    truth = read_table(TRUTH_TABLE) if TRUTH_TABLE in tables else []
    by_scenario = {}
    for t in truth:
        by_scenario[t["scenario_id"]] = dict(
            family=t.get("scenario_family"),
            truth_class=t.get("truth_class"),
            expected_detection=bool(t.get("expected_detection")),
            entities=len(str(t.get("entity_ids") or "").split(","))
            if t.get("entity_ids") else 0,
        )
    graph = {}
    db = DATA_ROOT / GRAPH_DB_NAME
    if db.exists():
        from ..graph import GraphStore
        g = GraphStore(db)
        graph = g.counts_by_synthetic()
        g.close()
    return dict(tables=tables, scenarios=by_scenario, graph=graph)


def format_generation(res: GenerationResult) -> str:
    lines = []
    w = res.world
    lines.append("=" * 72)
    lines.append(f"scenario generation — seed {w.seed}")
    lines.append("=" * 72)
    lines.append(f"corpus window : {w.t0:%Y-%m-%d} .. {w.t1:%Y-%m-%d}")
    lines.append(f"scenarios ran : {len(res.ran)} function(s), "
                 f"{len(w.truth)} truth row(s)")
    s = w.truth.summary()
    lines.append(f"  true anomalies {s['true_anomalies']}  |  "
                 f"decoys {s['decoys']}  |  "
                 f"deliberate misses {s['deliberate_misses']}")
    lines.append(f"  expected to fire: {s['expected_to_fire']}")
    lines.append("")
    # Every cohort, summed, and then reconciled against the hulls that actually
    # exist. The old line named three of the ten groups and then wrote
    # "= N total", so the arithmetic printed in the operator's face had not
    # closed since the commercial fleet was added and was out by four hundred
    # once the wider fleet was. A report whose own sum is wrong teaches its
    # reader to skim it, and skimming this report is how a silent corruption
    # gets through — so the mismatch is stated rather than left to be noticed.
    lines.append("cast")
    for label, n in cohorts():
        lines.append(f"  {label:<28}{n:>10,}")
    expected = expected_vessel_count()
    lines.append(f"  {'= total':<28}{expected:>10,}")
    if expected != len(w.vessels):
        lines.append(f"  MISMATCH: {len(w.vessels):,} hull(s) were actually "
                     f"minted — a cohort is missing from `cast.cohorts()` or a "
                     f"hull was minted outside one")
    lines.append("")

    lines.append("BUILT (in memory)")
    for k, v in sorted(res.built.items()):
        lines.append(f"  {k:<28}{v:>10,}")

    if w.radar is not None:
        lines.append("")
        lines.append(format_radar(w.radar))

    lines.append("")
    lines.append("LANDED (read back from disk — this is the real number)")
    disk = landed_counts()
    for table in sorted(disk):
        d = disk[table]
        lines.append(f"  {table:<28}{d['synthetic']:>10,} synthetic"
                     f"{d['real']:>12,} real")
    if not disk:
        lines.append("  (nothing landed — generate was run with land=False)")

    lines.append("")
    lines.append(res.world.profile.format_report())
    lines.append("")
    lines.append(res.validation.format())
    return "\n".join(lines)


def format_status(st: dict) -> str:
    lines = ["=" * 72, "scenario status", "=" * 72]
    lines.append(f"{'table':<32}{'real':>12}{'synthetic':>12}")
    for table, d in sorted(st["tables"].items()):
        lines.append(f"{table:<32}{d['real']:>12,}{d['synthetic']:>12,}")
    if not st["tables"]:
        lines.append("(no landed tables)")

    if st["graph"]:
        lines.append("")
        lines.append("graph")
        lines.append(f"{'':<32}{'real':>12}{'synthetic':>12}")
        for k, d in st["graph"].items():
            lines.append(f"  {k:<30}{d['real']:>12,}{d['synthetic']:>12,}")

    lines.append("")
    lines.append(f"scenarios: {len(st['scenarios'])}")
    if st["scenarios"]:
        lines.append(f"  {'id':<8}{'class':<18}{'family':<24}fires?")
        for sid, d in sorted(st["scenarios"].items()):
            lines.append(f"  {sid:<8}{d['truth_class']:<18}"
                         f"{d['family']:<24}"
                         f"{'yes' if d['expected_detection'] else 'no'}")
    lines.append("")
    lines.append("Counts are never blended: any figure quoted externally must "
                 "state which column it came from.")
    return "\n".join(lines)
