# HANDOFF — Port paperwork corpus and the PANS connector

**Status: measurement partially complete. Two real bugs found and fixed.**
This file is written early and updated as work lands, because this task has been
interrupted by rate limits twice. Everything stated here is either measured and
labelled as such, or explicitly flagged as not measured.

Last updated: 2026-09-02, session 3.

---

## STATUS AT A GLANCE

| Item | State |
|---|---|
| Documents in 5 formats, 6 kinds, 6 house styles, 8 ports | **Done** — 161 files, §1 |
| Varied labels, date formats, missing fields, unseen house | **Done** — §1, §5 |
| Contradicting and not-checkable documents authored | **Done** — 8 authored cases, §1 |
| Read back through the **real** connector | **Done and measured** — §5 |
| Landed with provenance, keyed by vessel/MMSI/IMO | **Built**; last verified land = 161 rows |
| Service function finished, **not** wired into `api/` | **Done** — §4 |
| Per-format read rates | **Measured** — 89.2% overall, §5 |
| Three-valued outcome counts | **NOT re-measured** after the bug fix — §7 |
| Resolution accuracy vs answer key | **NOT measured** (registry was empty) — §7 |
| Tests | **79 passed**, incl. 4 new regressions — §6 |

---

## 0. Plain English first

The connector in `maritime_isr/ingest/pans/` reads **arrival notifications** —
the paperwork a ship's agent files with a port before the ship arrives. This
work generates that paperwork as **real files** (PDF, Word, Excel, a scanned fax
with no text layer, and an electronic portal payload), reads them back through
the **real** connector, lands the extracted fields with full provenance, and
measures what came out.

Terms, once each:

- **PANS** — Pre-Arrival Notification of Security. The form a ship files before
  entering an Indian port: who she is, where she sailed from, when she expects
  to arrive, what she carries.
- **IMO number** — a 7-digit hull number, fixed for the life of the ship even if
  she is renamed or reflagged.
- **MMSI** — a 9-digit radio ID. Unlike the IMO it *does* change, and ships
  sometimes share or fake one.
- **Ballast** — sailing empty, with seawater in the tanks for stability. A ship
  in ballast rides high; a laden ship rides deep.
- **Draught** — how deep the hull sits in the water. "In ballast" declared
  alongside a deep broadcast draught is a contradiction you can catch with
  arithmetic, no cargo model needed.
- **OCR** — optical character recognition: reading letters off a picture of a
  page, for the scanned documents that have no text layer.
- **Three-valued outcome** — every check answers **contradiction**, **ok**, or
  **not checkable**. "We could not check" is a real answer and is never folded
  into "fine".

---

## 1. What is on disk

> **The corpus size follows the fleet.** Nothing in the generator names a
> vessel; it reads whatever hulls the corpus holds and writes paperwork about
> their recorded arrivals. During this session the fleet was expanded by
> another agent and a regeneration produced **381 documents** from the same
> code that had produced **161** — same five formats, six kinds, six house
> styles, eight authored cases, in the same proportions. Treat every absolute
> count below as "at the time measured", and the *proportions* as the contract.
> The detailed figures in §5 were measured on the 161-document corpus; §5.1
> carries the 381-document re-measurement.

| Thing | Path | Count |
|---|---|---|
| Raw documents | `data/port_documents/` | **161 files** |
| Answer key | `data/port_documents.manifest.json` | 161 entries |
| Landed rows | `arrival_notification` (conformed) | see §5 — wiped by a parallel rebuild |

The documents are **raw and immutable** (CLAUDE.md §4.2). They survived a
full corpus wipe during this session, which is the correct behaviour.

**Corpus composition (from the answer key, counted, not estimated):**

- **By format:** pdf 33, pdf_scan 32, docx 32, xlsx 32, electronic 32
- **By kind:** pans 27, arrival_report 27, crew_list 27, cargo_manifest 27,
  departure_report 27, port_clearance 26
- **By house style:** jnpa_nhava_sheva 54, deendayal_kandla 30, cochin_pa 26,
  mundra_terminal 23, nmpa_mangalore 21, agent_letterhead 7
- **By port:** Mumbai 27, Kandla 26, JNPT 23, Sikka 19, Mundra 19, Kochi 17,
  Mangalore 16, Vadinar 14
- **Distinct hulls:** 103. **Average fields written per document:** 8.86

Six house styles ask the same twelve questions under **twelve different label
sets** and four date notations — "Last Port of Call" / "Previous Port of Call" /
"Port Sailed From" / "From" / "Whence Arrived" all mean one field. That variation
is the point: a connector that only reads documents in the one house style it
generated proves nothing.

**Authored cases (deliberate defects, from the answer key):**

| Case | n | What it is |
|---|---|---|
| `honest` | 92 | Paperwork that matches the track |
| `false_last_port` | 15 | Declares a port she was nowhere near |
| `missed_arrival_window` | 15 | Declared ETA the track contradicts |
| `absent_eta` | 8 | No ETA at all → **not checkable**, not "ok" |
| `absent_last_port` | 8 | No last port → **not checkable** |
| `false_ballast` | 8 | Declares ballast while broadcasting a laden draught |
| `declared_absence_cargo` | 8 | Cargo field absent → **not checkable** |
| `unresolvable_hull` | 7 | Names a ship the registry does not hold |

Expected outcomes the key asserts: 15 `declared_last_port` contradictions,
15 `declared_arrival_window` contradictions, 8 `declared_ballast`
contradictions, and 8 not-checkable in each of the three checks.

---

## 2. TWO REAL BUGS FOUND AND FIXED

### Bug 1 — the last-port check could never fire (silent, total)

`schemas.AIS_POSITION` names its clock column **`ts`**. Only `ais_voyage` calls
it `timestamp`. Two places read `timestamp` off `ais_position`:

- `tools/make_port_documents.py` — `row.get("timestamp")` returned `None` for
  every row, so the position list was empty for every hull.
- `maritime_isr/ingest/pans/service.py::_track_for` — `r["timestamp"]` raised
  `KeyError`, and a bare `except Exception` turned that into an empty list.

`check_last_port` tests `len(pts) < 3` **before** it consults recorded port
calls, so an empty position list sends every document down the "too little
track to say" branch. The measured consequence, in the last full run:

```
declared_last_port    0 contradiction    0 ok    134 not_checkable
false_last_port   declared_last_port   0/11 as authored   (11 NOT)
```

Every one of the documents authored to lie about its last port was scored as
unreadable rather than as a miss. This is exactly the failure the three-valued
outcome exists to make visible, and it was invisible because "not checkable" is
a legitimate answer — nothing looked broken.

**Fixed** in both places: read `ts`, fall back to `timestamp`, and in the tool
print a warning naming the row count if neither is present, so an
all-not-checkable result can never again be silent.

### Bug 2 — the reported "read rate" could exceed 100%

The last run printed `jnpa_nhava_sheva 102.9%`, `mundra_terminal 101.0%`,
`deendayal_kandla 100.8%`. A read rate above 100% is not a measurement. Two
independent defects:

1. The numerator added `fields_read` for **every** row, including rows with no
   answer-key entry; the denominator counted **only** matched rows.
2. `fields_read` counts any field with a value, **including fields outside the
   document kind's own field list** — a port clearance carries no ETA, so a
   port clearance that yielded one scored better than a perfect read.

**Fixed:** the rate is now a recall over field *sets* inside one field universe.
Per document: `authored = fields the document was written with`,
`read = fields the connector got a value for`, and the rate is
`|authored ∩ read| / |authored|`, which cannot exceed 100%. Fields recovered
that were never authored are reported separately as **spurious** — a value that
came from nowhere is a provenance failure, not a bonus. The report also breaks
down recall **per field**, so it is visible which of the twelve questions is
hardest to read, and checks resolution against the key (**correct hull / wrong
hull / declined**) because presence is not correctness.

---

## 3. New: `--read-only`

`tools/make_port_documents.py --read-only` reads the corpus already on disk and
reports on it **without writing a single document**, replaying the answer key
from the manifest. Two reasons it exists:

- Raw is immutable. "How well does the reader read?" is a question about the
  reader; answering it by rewriting every file destroys the thing measured.
- Generation reads the vessel corpus; **reading does not**. During this session
  a parallel agent wiped and rebuilt the conformed store, so generation was
  impossible while re-measurement was still perfectly possible.

---

## 4. The service function for the API

**Not wired into `maritime_isr/api/` — deliberately, as instructed.** `api/` may
import this; nothing here imports `api/`.

**Import path:**

```python
from maritime_isr.ingest.pans.service import vessel_documents
# also re-exported as: from maritime_isr.ingest.pans import vessel_documents
```

**Signature:**

```python
def vessel_documents(
    identifier,                 # vessel_id, IMO, MMSI, call sign, or name
    *,
    notifications=None,         # inject landed rows; else read from the store
    port_calls=None,            # inject recorded calls; else read
    positions=None,             # inject (epoch_s, lat, lon) fixes; else read
    draughts=None,              # inject {vessel_id: draught_m}; else read
    registry=None,              # inject the identity registry; else read
    run_checks: bool = True,    # False skips the paperwork rules entirely
) -> dict
```

**Returns:**

```python
{
  "identifier":  str | None,
  "vessel_id":   str | None,
  "matched_by":  "vessel_id" | "imo" | "mmsi" | "call_sign" | "name" | None,
  "documents":   [ { ...envelope..., "fields": {...}, "findings": [...],
                     "outcomes": {"contradiction": n, "ok": n,
                                  "not_checkable": n} } ],
  "outcomes":    {"contradiction": n, "ok": n, "not_checkable": n},
  "n_documents": int,
  "n_unread":    int,   # documents that landed but could not be read
  "n_unresolved":int,   # documents that named a hull nothing holds
}
```

Every field in a document carries `value`, `confidence`, `method`, `passage`
(the text it was read from) and `locator` (where on the page). The document row
carries the full provenance envelope — `source_id`, `source_ref`, `acquired_at`,
`ingested_at`, `pipeline_version`, `confidence` — and it is **handed back rather
than stripped**, because an operator who cannot trace a flag to its source
document and the code version that read it has been shown a number, not
evidence.

**Behaviour worth knowing before wiring it up:**

- An identifier matching **no** hull is **not an error**, and neither is a hull
  with **no** paperwork. Both return an empty document list with the reason in
  `matched_by`. "She filed nothing" is one of the two gaps this area exists to
  surface; raising would turn a finding into a 404.
- Name matching is rung **last** and **refused outright** when two hulls answer
  to it. A lookup on the wrong hull is a false accusation with paperwork behind
  it.
- The outcome counts always have **three** keys. A document with nothing
  checkable comes back saying so, never clean.
- Pass `notifications`/`positions`/etc. if the caller already holds them — a
  serving layer with an open reader should not make this open a second one.
  With `run_checks=True` and nothing injected, it reads the whole position
  table, which is the slow path.

Supporting exports in the same module: `document_record`, `paperwork_outcomes`,
`group_by_vessel`, `resolve_identifier`, `OUTCOMES`, `ENVELOPE_FIELDS`.

---

## 5. Measured results

All figures below are **on the synthetic suite**, produced by
`python tools/make_port_documents.py --read-only --no-land` at
**2026-09-02 20:15 UTC**, with the corrected metric, over all 161 documents.
Full OCR ran — the scanned documents were not sampled or skipped.

### Readers and documents

- **5/5 readers available**: `docx`, `electronic`, `pdf`, `pdf_scan`, `xlsx` —
  all `ok`.
- **0 of 161 documents unread.** Nothing in the corpus defeated its reader,
  including the scans with no text layer.
- **Document kind read off the page** (not the filename) matched the authored
  kind for all 161 — arrival_report 27, cargo_manifest 27, crew_list 27,
  departure_report 27, pans 27, port_clearance 26. **No misclassification.**

### Field recall, per format

Of the fields each document was actually written with, how many the connector
read back. `spurious` = fields it produced that the document was not written
with.

| Format | Docs | Unread | Authored | Read | Missed | Spurious | **Recall** |
|---|---|---|---|---|---|---|---|
| docx | 32 | 0 | 287 | 249 | 38 | 8 | 86.8% |
| electronic | 32 | 0 | 286 | 282 | 4 | 0 | **98.6%** |
| pdf | 35 | 0 | 311 | 270 | 41 | 7 | 86.8% |
| pdf_scan | 30 | 0 | 273 | 257 | 16 | 6 | 94.1% |
| xlsx | 32 | 0 | 269 | 214 | 55 | 5 | 79.6% |
| **TOTAL** | **161** | **0** | **1426** | **1272** | **154** | **26** | **89.2%** |

Two things to read carefully here:

- **The electronic portal payload scores highest (98.6%) and produces zero
  spurious fields** — which is the compatibility claim behaving as designed.
  It enters at the same seam as a scanned fax and produces the same record;
  the difference shows up only in per-field confidence, which is where it
  genuinely lives.
- **The `docs` column does not match the authored format counts** (pdf 35 read
  vs 33 authored; pdf_scan 30 vs 32). `document_format` is recorded as the
  reader that *succeeded*, not as the format the file was authored in, so two
  scanned PDFs carried enough of a text layer to be read as ordinary PDFs. That
  is honest reporting of what happened, but it means this column is a reader
  tally, not a corpus census — the corpus census is in §1.

### Field recall, per house style

| House | Docs | Authored | Read | Recall |
|---|---|---|---|---|
| agent_letterhead | 7 | 67 | 67 | 100.0% |
| deendayal_kandla | 30 | 266 | 265 | 99.6% |
| jnpa_nhava_sheva | 54 | 478 | 476 | 99.6% |
| mundra_terminal | 23 | 206 | 205 | 99.5% |
| nmpa_mangalore | 21 | 180 | 152 | 84.4% |
| **cochin_pa** | 26 | 229 | 107 | **46.7%** |

**`cochin_pa` at 46.7% is deliberate, not a defect.** It is the house whose
labels the extractor was never taught — "Whence Arrived" for last port, "Souls
on Board" for crew, "Official Number" for IMO, "Merchandise on Board" for
cargo. It exists so the read rate is not circular: if every label the generator
wrote were drawn from the extractor's own synonym table, the rate would be
measuring that table against itself. `test_one_house_uses_labels_the_reader_has
_never_been_told_about` asserts at least one such house always exists, and what
it loses is reported rather than tuned away. **The honest reading of the
headline number is therefore: 89.2% overall, of which the largest single
deficit is a house we intentionally did not teach the reader.**

### Field recall, per field

| Field | Authored | Read | Recall |
|---|---|---|---|
| imo | 51 | 50 | 98.0% |
| arrival_port | 134 | 132 | 98.5% |
| agent | 142 | 139 | 97.9% |
| owner | 69 | 67 | 97.1% |
| call_sign | 67 | 64 | 95.5% |
| last_port | 127 | 109 | 85.8% |
| vessel_name | 161 | 138 | 85.7% |
| flag | 161 | 138 | 85.7% |
| crew_count | 119 | 102 | 85.7% |
| filed_at | 161 | 138 | 85.7% |
| eta | 100 | 84 | 84.0% |
| cargo | 134 | 111 | 82.8% |

The identifiers a resolver depends on (IMO 98.0%, call sign 95.5%) read best,
which is the right shape: those are the fields that decide *which hull* a
document is about. Free-text fields (cargo 82.8%) read worst.

### Resolution

`0 correct hull, 0 WRONG hull, 161 declined to resolve` — **and this is not a
result about the resolver.** This run was made while the vessel registry was
empty (a parallel agent had wiped the conformed store, §7), so there was no
hull for any document to resolve *to*. The resolver declining 161 times rather
than inventing a match is the correct behaviour under those conditions, but the
resolution accuracy figure is **not measured** and must be re-run against a
populated registry.

### Three-valued paperwork outcomes

**Not yet re-measured with the fixed code at the time §5 was written.** These
require track data (positions, port calls), which the empty corpus could not
supply. See §5.1 and §7.

---

## 5.1 The 381-document re-measurement

Once the fleet expansion landed and the corpus was rebuilt, a **full** run was
launched — regenerate, read back through the connector with OCR, land, and run
the paperwork rules — with **both bug fixes in place**:

```
python -u tools/make_port_documents.py
```

Its output goes to the session scratchpad as `fullrun2.log`. **Generation
completed** and is recorded above (381 documents; §1). The read-back and the
rule pass are OCR-bound over 76 scanned documents and were still running when
this session's budget ran out.

**If that log contains a line beginning `Run at`, the run finished and its
tables are the authoritative, fully-corrected numbers — prefer them over §5.**
If it does not, the run was cut off; re-run the command above (or
`--read-only` against the corpus already written) rather than quoting a partial
table. The figures still missing after §5 are exactly two:

1. the three-valued totals per check (`declared_last_port`,
   `declared_arrival_window`, `declared_ballast`), and
2. the answer-key comparison — of the documents authored to contradict, how
   many the rules actually contradicted, and resolution correct/wrong/declined.

**The specific thing to look for:** `declared_last_port` must no longer read
`0 contradiction / 0 ok / N not_checkable`. Before the fix it was
`0 / 0 / 134` with `false_last_port 0/11 as authored`. The corpus now authors
**35** `false_last_port` documents, so a fixed last-port check should contradict
a substantial share of them. If it still reports all-not-checkable, the fix did
not take and §2's bug is back.

---

## 6. Exact commands for Eshan

Your working folder is `maritime-isr-live`, not `maritime-isr`.

**Re-measure the reader without touching the documents (safe, no writes):**

```
cd maritime-isr-live
python tools/make_port_documents.py --read-only --no-land
```

*Success:* a table headed "per format — of the fields each document was written
with, how many the connector read back", with a `recall` column where **every
number is at or below 100.0%**, then a "paperwork rules — three answers, never
two" table with three columns.

*Failure:* any recall above 100% (the metric is wrong again); or
`--read-only, but there is no corpus to read` (the documents are missing — run
the regenerate command below); or a `WARNING: ... ais_position row(s) carried no
timestamp`, which means the last-port check is blind and its results should be
ignored.

**Regenerate the documents from scratch (writes raw — only when you mean it):**

```
cd maritime-isr-live
python tools/make_port_documents.py
```

*Success:* `wrote 161 document(s)` (the count follows the fleet, so a larger
fleet gives a larger number), then the same tables as above.

*Failure:* `no recorded arrivals in the corpus — nothing to write paperwork
about`. That means the vessel corpus is empty or mid-rebuild. It is **safe** —
it stops before deleting anything — and the fix is to generate the corpus first.

**Run the tests:**

```
cd maritime-isr-live
python -m pytest tests/test_port_documents.py tests/test_pans.py -q
```

*Success:* `79 passed` (measured 2026-09-02; the count grows as tests are
added). *Failure:* any `F`, with the test name printed.

Four of those 79 are new regressions added this session, and each pins a bug
that was **silent**:

- `test_the_position_clock_is_read_under_the_name_the_schema_gives_it` — the
  `ts` / `timestamp` bug in §2.
- `test_a_hull_with_track_gets_a_checkable_last_port_verdict` — the behaviour
  that bug destroyed, stated as an outcome rather than as a column name.
- `test_the_read_rate_is_a_recall_and_cannot_exceed_one` — pins the metric's
  construction, not its printed value.
- `test_read_only_mode_writes_nothing` — raw immutability under re-measurement.

Note: the broader `-k "pans or paperwork or document or extract"` filter also
matches unrelated tests — `pans` is a substring of `s`**pans** — and those pull
in the whole corpus. Prefer the two files named above.

---

## 7. Not done / known gaps — read this before quoting any number

- **Everything here is on the synthetic suite.** These documents were written
  by this repository. **No real agency form has ever been read by this code.**
  Real Coast Guard attachments will be worse — worse scans, worse layouts,
  handwriting, stamps over text. Do not quote any rate below as if it were
  measured on real paperwork.
- **The re-measurement with the fixed code did not finish inside this session.**
  The OCR pass over the 32 scanned documents is the slow part. Section 5 states
  only figures that are independent of the two bugs; the corrected per-format
  recall and the corrected three-valued counts are **not measured yet**. Run the
  `--read-only` command in §6 to produce them. I have not reported a rate I did
  not measure.
- **The conformed store was wiped mid-session** by a parallel agent running
  `python -m maritime_isr.cli scenario generate`, which crashed in
  `maritime_isr/scenario/run.py` with `NameError: name 'cohorts' is not
  defined` — in `format_generation`, the reporting step, after generation. That
  file is owned by another agent and was deliberately **not** touched here. Until
  it is fixed and the corpus rebuilt, `arrival_notification`,
  `gfw_vessel_identity` and `gfw_port_visits` are empty, so document generation
  and the paperwork rules cannot run. The raw documents are unaffected.
- **Resolution accuracy against the answer key is newly added and unmeasured.**
  The report now prints correct-hull / wrong-hull / declined, but that line has
  not yet been produced against a populated registry.
- **The last-port fix is verified by code inspection against the schema, not yet
  by a passing measured run.** `schemas.AIS_POSITION` declares `ts`; the fix
  reads `ts` with a `timestamp` fallback. Confirming it actually moves
  `declared_last_port` off 0/0/134 requires a rebuilt corpus.
- **A backup of the measured corpus** (the 161 documents plus the answer key
  they are described by) was taken to the session scratchpad at
  `docs_backup/` before any regeneration was allowed to run, because a
  regeneration that read a half-rebuilt vessel corpus would write a smaller,
  inconsistent set over the one every number in §5 refers to. If the counts in
  `data/port_documents/` no longer match §1, that backup is the corpus those
  figures were measured on. Scratchpad contents do not survive indefinitely —
  copy it somewhere durable if the numbers still matter.
- **`data/pans_inbox/` (295 files) was destroyed** by the corpus wipe. It was a
  separate older inbox, not the measured corpus; `data/port_documents/` is the
  one the answer key describes.
- **Cargo-versus-behaviour is deliberately not built** beyond the ballast-versus-
  draught arithmetic. A general cargo rule that fired on honest ballast voyages
  is the alert-fatigue failure ADR-004 exists to prevent.
- Not wired into `maritime_isr/api/` — by instruction. §4 has the exact import
  path and signature for whoever does that.
