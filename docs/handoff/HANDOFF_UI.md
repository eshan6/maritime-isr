# HANDOFF — Section 3 capabilities on the screen

**Branch:** `claude/system-tech-overview-4w7amz`
**Scope of this work:** `frontend/**`, `maritime_isr/api/**`, `tests/test_api_analysis.py`.
Nothing outside those was touched. `STATE.md`, `COMMITS.md`, `DECISIONS.md`,
`ARCHITECTURE.md`, `README.md` and `CLAUDE.md` are deliberately **not** edited —
the parent session owns those.

---

## 1. What this was for

A large part of what the Python side can already do had no way to a screen. It
ran, it produced an answer, and the answer stopped at the process boundary. That
is not a small cosmetic gap, because of *which* half was missing.

Every rule module in `anomaly/` returns **three** outcomes — `contradiction`,
`ok`, and `not_checkable` — and only the first one ever became an alert. So a
screen built on alerts alone was showing an officer contradictions and silence,
with no way to tell "we checked her and she is fine" apart from "we could not
check her at all". The same shape repeats across the build: `is_unusual` on a
per-area baseline is three-valued and `None` means *we have not watched here
enough to have an opinion*; `activity` has `unclassified` as a real answer;
`vessel_type` deliberately reports a merged coarse label where motion genuinely
cannot separate a laden bulker from a laden product tanker.

In every one of those cases the honest half is the valuable half, and it was the
half that never left the terminal. That is what this change puts on the glass.

---

## 2. Gap table

"existed" = was already there before this work. "added" = added by this work.

| Capability | Backend module | API endpoint | UI location |
|---|---|---|---|
| Identity authenticity — IMO check digit, MMSI MID vs declared flag, registry consistency per field | `anomaly/identity.py` | `GET /api/vessels/{id}/checks` **added**; `GET /api/checks/coverage` **added** | Watch → a subject → **"What was checked"** panel **added**; Method → **"What can be checked, across the whole record"** **added** |
| Declared voyage vs track (destination, ETA) | `anomaly/voyage.py` | same two **added** | same two **added** |
| Paperwork — arrival notification vs track | `anomaly/paperwork.py` | same two **added** | same two **added**; each finding shows the **passage** it was read from and the document it came off |
| Imagery vs declared type | `anomaly/imagery.py` | same two **added** | same two **added**; the capture is rendered inside the finding, stamped simulated |
| Vessel type from motion alone — the merged vocabulary and the confusion matrix | `tracks/vessel_type.py` | `GET /api/analysis/vessel-type` **added** (measurement is opt-in, `?compute=true`) | Method → **"Vessel type from motion alone"** **added** — vocabulary, "what it cannot separate, and says so", and the matrix it was read off |
| Activity classification, `unclassified` first-class | `tracks/activity.py` | `GET /api/vessels/{id}/motion` **added** | Watch → a subject → **"What she is doing"** **added**; `unclassified` renders in the third state, not as a blank |
| Vessel-to-vessel interactions | `tracks/interactions.py` | `GET /api/analysis/interactions` **added** | Method → **"One hull against another"** **added** — the four behaviours, their gates, and the measured silence on this corpus stated as a fact about the corpus |
| Forward projection and its uncertainty cone | `tracks/projection.py` | `GET /api/vessels/{id}/motion` **added** | Watch → a subject → **"Where dead reckoning puts her"** **added** — radius and confidence per lead, with the "this is an expectation, not a suspicion signal" caveat on the screen rather than in a tooltip |
| Per-area baselines, three-valued `is_unusual` | `baselines.py` | `GET /api/baselines` **existed** | Method → **"What normal looks like, per area"** **added** (first UI consumer of this endpoint); Watch → a subject → **"local baseline"** row **added**, with `no_opinion` and `no_layer` in the third state |
| Contact profiles — the contact that correlates to nothing | `fusion/contact_profile.py` | `GET /api/radar/contacts/{candidate_id}/profile` **added** | Radar → a dark contact → **"what can be said about her"** **added**; it carries `profiles_not_detects` and lists what could not be established |
| The EO / camera loop, including **why** the camera was pointed there | `eo/cue.py`, `capture.py`, `classify.py` | `GET /api/eo/captures` **added**; `GET /api/eo/summary` **added** | Method → **"The camera loop"** **added**; Watch → a subject → **"Camera looks"** **added** |
| Attribution — `origin` (outside body) vs `derivation` (what we then did) | `assistant/attribution.py` | carried on **every** response above; `EvidenceHop.derivation` **added** to `api/models.py` | `SourceLine` component **added**, rendered under every panel listed above; alert evidence hops now carry both halves |

### The one thing to know about the check surface

`contradiction`, `ok` and `not_checkable` are three visually distinct states,
and they differ by **shape** as well as by hue — solid left rule, solid left
rule, **dashed** left rule; ✕, ✓, **?** — because colour alone cannot carry a
three-way distinction for a colour-blind reader, and several of the family hues
already sit under 3:1 contrast. Collapsing the third into a green tick is
exactly the failure ADR-032 (b) warns about, and the tests in
`tests/test_api_analysis.py` assert the three stay three on the wire.

---

## 3. Also done, by instruction

**"Recommended actions" is gone from `WatchView.jsx`.** The UI block only. The
backend still builds `v.recommendations` in `assistant/recommend.py`, the API
still returns the field, the factor catalog is still keyed on those actions, and
the incident report still prints them. A comment at the removal site says so, so
nobody restores it thinking a capability was lost. This is a visibility
decision, not a deletion.

**Dead code removed.** `analysis.attribution_of()` had no caller and no route.
Its import of `assistant.attribution.describe` went with it.

**One robustness fix.** The projection panel's "the cone grows at *n* nm per
hour" sentence now **reads** `CONE_GROWTH_M_PER_HOUR` out of
`tracks/projection.py` instead of quoting the number from memory. See §6 — that
module is being rewritten under this branch, and a serving-layer sentence that
quotes a tuning constant becomes a quiet lie the first time somebody retunes it.

---

## 4. Verification actually run (not assumed)

Both of these were run to completion in the sandbox, on this working tree:

```
cd frontend && npm run build          ✅ built in 19.35s, dist/ regenerated
python -m pytest tests/test_api_analysis.py -q   ✅ 14 passed, 2 skipped
python -m pytest tests/ -q -k "api or serve or analysis"
                                       ⚠ 63 passed, 11 skipped, 1 failed
```

**Status honestly: built + verified in sandbox. Not verified on host.** Nothing
here has run on the Oracle VM (it is not provisioned) or against a live feed.

### The one failure, and why it is not this work

`tests/test_api_exercise.py::test_ports_non_empty_and_split` fails with
`assert []` — the port gazetteer is empty. `service.list_ports()` reads port
nodes out of `graph.sqlite`, and **there is no `graph.sqlite` in this sandbox's
`data/` at all**. The graph has not been built here. `/api/ports` was not touched
by this work. This is corpus state, not a regression — another agent is rebuilding
the corpus under this branch. It should be re-checked once that lands.

### The two skips

Both are corpus state, both self-reported by the test:

- `no captures landed` — `data/conformed/eo_capture/` has **0 rows**.
- `no alerts landed` — no `graph.sqlite`, so no alert queue.

---

## 5. What could NOT be surfaced, and which kind of missing it is

The distinction matters: a missing *surface* is my problem, a missing
*endpoint* is my problem, and missing *data* is not — the surface is built and
renders its honest empty state.

| Thing | Kind of missing | What actually happens on screen today |
|---|---|---|
| Paperwork findings | **Missing data.** `data/conformed/arrival_notification/` has 0 rows in this sandbox. | The "Arrival notification" group renders in the third state with its own note. It does **not** render as a pass. |
| Camera captures | **Missing data.** `data/conformed/eo_capture/` has 0 rows. | Method → "The camera loop" renders `not_checkable` with the disclosure still shown; the subject panel says "No camera was ever pointed at her." |
| Per-area baselines | **Missing data.** `data/conformed/area_baseline/` has 0 rows. | The baseline row renders `no_layer`. The Method card shows the endpoint's own `note` telling the operator to run `baselines derive`. |
| Contact profiles | **Missing data.** `data/conformed/radar_dark_contact/` has 0 rows. | The Radar tab has no contacts to expand. The endpoint and the panel are both in place and were exercised by unit-level code paths, not by a populated corpus. |
| Alerts / the Watch queue | **Missing data.** No `graph.sqlite`. | Watch is empty. Everything hanging off a subject is therefore unexercised end-to-end in this sandbox. |
| A drawn uncertainty cone on the map | **Missing surface, deliberately not built.** | The numbers behind the cone (radius and confidence per lead) are on the subject page; the map already has its own prediction layer (`/api/predictions`, ADR-039) and putting a second cone renderer next to it would be two answers to one question. Left for whoever owns the map layer. |
| A live camera image | **Missing capability, permanently.** There is no camera. | Every capture frame is hatched and stamped **"simulated · no image"** on the frame itself, and `image_ref` is asserted empty by test. The disclosure is repeated on every response and every card. It is never a footnote. |

---

## 6. What the parent session must wire once the other three agents land

1. **Rebuild the corpus and the graph, then re-run the two verification
   commands.** Most of what is listed in §5 as "missing data" should populate.
   Anything that does not is then a real finding.
2. **`tracks/projection.py` is being rewritten.** `api/analysis.py` builds
   against the *current* interface:
   `project_from(lat=, lon=, sog_kn=, cog_deg=, made_at=, valid_for=,
   track_id=, track_source=)` returning an object with `.lat`, `.lon`,
   `.radius_m`, `.confidence`; plus the module constant
   `CONE_GROWTH_M_PER_HOUR`. If any of those five names or the constant move,
   `_projection_for()` in `maritime_isr/api/analysis.py` breaks and
   `test_the_projection_ships_the_caveat_that_stops_it_being_read_as_a_signal`
   is the test that will catch it. Note the module has also grown
   `project_route_aware()`; this surface deliberately uses plain dead reckoning,
   because the panel says "dead reckoning" and it must mean it.
3. **`frontend/dist/` was rebuilt and must be committed.** The backend serves
   the committed `dist/`, so a commit that takes `src/` without `dist/` ships a
   UI that does not contain any of this.
4. **Check `STATE.md` and `COMMITS.md`.** This work did not touch them and it
   needs an entry in both. Suggested status wording: *"built + verified in
   sandbox; unverified on host"* — the exit test needs the VM, which does not
   exist yet.
5. **Nothing here is documented in `README.md`.** The Method tab is a new
   sixth tab and a reader following the README will not know it exists.

---

## 7. How Eshan sees it

Run these **in this order**, in a terminal, and let each finish before starting
the next. If a step prints an error, stop there and paste the whole message
back — do not continue to the next step.

```bash
cd maritime-isr-live
```

**Step 1 — install the API bits (once).**

```bash
python -m pip install -e ".[api]"
```
*Success:* it ends with a line starting `Successfully installed`.
*Failure:* anything ending in `ERROR:`.

**Step 2 — land the data (a few minutes; only if you have not already).**

```bash
python -m maritime_isr.cli scenario generate --seed 7
python tools/run_scenario_pipeline.py
```
*Success:* the second command finishes and prints counts of what it landed.
*Failure:* `no landed ais_position data` means step one of the two did not
finish — run `scenario generate` again and let it complete.

**Step 3 — start the server.**

```bash
python -m maritime_isr.api
```
*Success:* it prints `Uvicorn running on http://127.0.0.1:8000` and then sits
there. **Leave this window open** — it is the server. It looks like it has
frozen; it has not.
*Failure:* it exits back to the prompt, or says `Address already in use` (you
already have one running — use that one).

**Step 4 — open the browser** at <http://127.0.0.1:8000>.

Then look at these four things, which are the new ones:

1. **The `Method` tab** in the top bar — this is new. It is not a queue and
   nothing on it can be clicked to action. It answers *what is this able to tell
   me, and where does it stop.* On it:
   - "What can be checked, across the whole record" — a three-colour bar per
     rule family. **The grey dashed portion is the important one**: those are
     hulls the rule could not run on at all. If that portion is large, that is
     the honest finding, not a bug.
   - "Vessel type from motion alone" — press **"Measure on the landed corpus"**.
     It takes tens of seconds and then shows the vocabulary the system will
     admit to, what it **cannot** separate, and the confusion matrix behind it.
   - "The camera loop" — note the amber bar at the top: **there is no camera.**
2. **The `Watch` tab** → click any vessel. Below the score arithmetic there are
   three new panels: **What was checked**, **What she is doing**, and **Camera
   looks**. If the corpus is thin, several of these will say "not checkable" —
   that is the correct answer and it is the point of the change.
3. **The `Radar` tab** → click a dark contact → the link **"what can be said
   about her"**. It describes her from motion alone and lists what it could not
   establish.
4. **The theme toggle**, top right. Cycle System → Light → Dark. Every new
   surface above should stay readable in all three; if any panel goes grey-on-grey
   or loses its colour, that is a bug worth reporting.

**What "Recommended actions" used to be:** it is gone from the vessel panel, as
you asked. The system still works it out behind the scenes and it still appears
in the exported incident report — it is just off this screen.
