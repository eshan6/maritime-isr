# GLOSSARY.md — Plain-English Terms

Every domain term used anywhere in this project, explained for someone with no
maritime, radar, or ML background. When Claude Code uses one of these in a session,
it should give the plain-English version alongside — the operator is non-technical
by design.

Grouped by area. Alphabetical within each group.

---

## Ships broadcasting their position (AIS)

**AIS (Automatic Identification System)** — the signal ships legally broadcast
saying "I am here, going this fast, in this direction." Like a car's license plate
that also announces its speed and location, on radio. The catch: a ship can switch
it off. A ship that goes silent to hide is the whole point of this project.

**MMSI (Maritime Mobile Service Identity)** — a ship's 9-digit radio ID, the number
in every AIS broadcast. Think of it as the ship's phone number. It *can* be changed
or faked, which is itself a signal worth watching.

**IMO number** — a permanent ship ID that (unlike MMSI) is supposed to stay with the
hull for life, even through renames and reflaggings. When a ship's MMSI changes but
we can tie it to the same IMO, that's an identity-change event, not two ships.

**SOG / COG / heading** — Speed Over Ground (how fast, in knots), Course Over Ground
(the direction it's actually travelling), and heading (the direction the bow points).
COG and heading differ when a current or wind pushes the ship sideways.

**Nav status** — a code in the AIS message for what the ship is doing (under way,
at anchor, moored, fishing, etc.).

**Spoofing** — faking AIS data: broadcasting a false position, or two ships using
the same MMSI. We **log** spoofing tells rather than discarding them — they're
evidence, not noise.

**Dark vessel / going dark** — a ship that has turned its AIS off (or is faking it)
so it can't be tracked by its broadcast. Finding these is the product. A ship is
only called "dark" when radar sees something the AIS picture can't explain **and**
we can rule out innocent reasons for the silence.

**Intentional silence** — AIS gap that we judge deliberate, because we can prove we
*would* have heard the ship here if it were broadcasting. Only asserted **inside
known coverage** (see coverage model). Offshore, where we can't hear anyway, a gap
is "unknown," not "dark."

**Rendezvous / encounter** — two ships coming very close and slow (in this system:
within ~500 m at under 2 knots), the signature of a possible ship-to-ship transfer.
A **dark rendezvous** — where one or both ships are AIS-silent — is a classic
sanctions-evasion move.

---

## Seeing ships from space (SAR)

**SAR (Synthetic Aperture Radar)** — a satellite radar that images the sea surface
day or night, through cloud, regardless of whether a ship is broadcasting. It sees
the *physical ship*, not its signal — which is exactly why it catches dark vessels.
A metal ship on water shows up as a bright spot.

**Sentinel-1** — the free European SAR satellite we use. It passes over our area
roughly every 2–4 days, so we get a **picture with gaps**, not a live feed.

**GRD / IW mode / VV / VH** — the specific Sentinel-1 data product and settings we
pull. GRD is a processed image product; IW is the imaging mode over coastal seas;
VV and VH are two radar "polarizations" (roughly, two ways of illuminating the
scene that reveal slightly different things).

**Revisit** — how often the satellite passes over the same spot again (~2–4 days
here). Sparse revisit is why this is a persistent picture with gaps, and the
architecture is built around that.

**Sigma-nought (σ⁰)** — the calibrated measure of how much radar signal a patch of
surface bounced back — essentially radar brightness. Ships bounce back strongly;
calm water bounces back little. Detection works on sigma-nought values.

**Preprocessing chain** — the fixed sequence that turns a raw SAR download into a
clean, correctly-positioned brightness image: apply orbit file (fix the satellite's
exact position) → remove thermal noise (sensor static) → calibrate to sigma-nought
→ terrain-correct (put each pixel at its true geographic spot).

**Terrain correction (geocoding-only here)** — repositioning pixels to their true
map location. We deliberately do **only** the positioning, not "radiometric
flattening," because flattening triggers an elevation-data download that hangs, and
over open ocean there's no terrain to flatten anyway. (DECISIONS ADR-006.)

**SNAP / pyroSAR** — SNAP is ESA's free SAR-processing toolkit; pyroSAR is the
Python wrapper we use to drive it reliably from scripts. (DECISIONS ADR-007.)

**COG (Cloud-Optimized GeoTIFF)** — the image file format we save preprocessed
scenes in; structured so tools can read just the piece they need without downloading
the whole file.

**Contact** — a bright, ship-like blob the detector found in a SAR image. A contact
is a *candidate*, not yet an identified ship.

**Detectability floor** — the smallest ship SAR can reliably see. At Sentinel-1's
~10–20 m resolution that's roughly a 15–25 m vessel; smaller craft are below the
physics limit, not an engineering gap. Stated as a product boundary, not hidden.

---

## Turning blips into ships (detection & tracking)

**CFAR (Constant False Alarm Rate)** — the baseline detector. For each pixel it
asks "is this much brighter than the water around it than random chance would
explain?" Transparent and tunable — it gives an honest floor before any ML.

**Land mask** — a map of where the coastline is, so the detector ignores land.
Get this wrong and breakwaters, islets, and fish farms flood the system with false
ships. It's the make-or-break step for false-positive rate.

**Azimuth ambiguity** — a radar artifact: a ghost blip that appears offset from a
strong real reflector. A source of false contacts the CNN learns to kill.

**CNN (Convolutional Neural Network)** — the learned image classifier we run after
CFAR to throw out false contacts (sea clutter, ghosts, fixed structures) by looking
at a small image patch ("chip") around each candidate.

**Chip** — a small cut-out image centered on a candidate contact, fed to the CNN.
When building training data we split by **scene**, never by chip, to avoid leakage.

**xView3 / SSDD** — public benchmark datasets of SAR imagery with labeled ships,
built for exactly the dark-vessel problem. We train and measure against them.

**Track** — a single ship's path stitched together from its AIS position reports
over time. Detections are photographs; tracks are the **memory**.

**Kalman filter / smoothing** — the math that turns noisy, gappy position reports
into a clean estimated path, and — crucially — estimates where the ship *probably
is now* between reports, with a growing margin of error.

**Uncertainty cone** — that growing margin. The longer since a ship's last report,
the larger the area it could now be in. This cone is the core input to matching
radar contacts against ships: it defines who *could* be at a given blip.

**Track fragmentation** — when one real ship's path gets broken into several
disconnected tracks (e.g. from gaps). We measure and keep this low.

**Coverage model** — an honest map of where our free AIS receivers can actually
hear ships. Silence only means something where we have ears. This map is a
proprietary asset — nobody else has it for Indian waters specifically.

---

## Matching radar to AIS (the fusion core, Phase 3)

**Association** — the central act of the product: for each radar contact, deciding
*which broadcasting ship it is* — or that it matches none (a dark candidate).

**Gating** — the first cheap filter: given each ship's last-known position and its
uncertainty cone, which ships could physically be at this contact at all? Rules out
the impossible before scoring the plausible.

**Scoring** — grading each surviving candidate by how well it fits: position
likelihood, whether the radar-estimated ship length matches the registry length,
heading consistency, and whether ships are usually seen in this spot.

**Hungarian / Jonker-Volgenant (JV) assignment** — the algorithm that assigns
contacts to ships **all at once, optimally across the whole scene**, instead of
one-at-a-time. Greedy one-at-a-time matching double-books ships and invents fake
dark vessels — it's banned here. (CLAUDE.md §6.)

**Matched / ambiguous / unmatched** — the three verdicts per contact: confidently
one ship / a short list of possibles / nobody — where "unmatched, and innocent
reasons ruled out" = **dark-vessel candidate.**

**Static-object layer** — a self-building list of things that always show up in the
same spot (oil rigs, buoys, wrecks) so they aren't mistaken for mysterious dark
ships. It accumulates from repeated same-position detections.

**Precision vs recall** — precision = of the alerts we raise, how many are real;
recall = of the real dark vessels, how many we catch. This project deliberately
favors **precision** (few false alarms) over recall (catching everything), because
false alarms destroy trust faster than misses do. (DECISIONS ADR-004.)

---

## Detections become intelligence (the graph, Phase 4+)

**Object graph / ontology** — a network of real-world things (Vessel, Organization,
Person, Port, Voyage, Encounter, Detection, Track, Alert, Source) and the labeled
relationships between them (owned-by, operated-by, flagged-to, docked-at, met-with,
sanctioned-under, formerly-identified-as, etc.). The ontology is the agreed
vocabulary of types and relationships.

**Edge** — one relationship in the graph (Ship A *met-with* Ship B). Every edge
carries where it came from (provenance), how sure we are (confidence), and when it
was true (valid_from/valid_to). No "naked facts."

**Provenance** — the record of where a fact came from and which version of the code
produced it. The product **is** trust, so nothing enters without it.

**Confidence decay** — old, unrefreshed facts automatically lose confidence over
time on a set schedule, so stale information doesn't masquerade as current truth.
The built-in defense against silent corruption.

**Behavioral fingerprint** — a vessel's characteristic pattern of movement (typical
speeds, routes, loitering, port calls) built up over time. Hard for a one-time spoof
to fake — the longer the system runs, the stronger this asset. It can't be
backfilled, which is why the graph turns on early.

**Identity laundering** — a ship changing its MMSI, name, or flag to shed a bad
history. We record each change as a first-class event (a formerly-identified-as
edge), turning the evasion tactic into a detectable signal.

**Sanctions list (OFAC SDN / UN / EU consolidated)** — official government lists of
entities you're not allowed to do business with. Ownership or contact links to a
sanctioned entity raise a vessel's risk. Stored with **as-of dates** because
sanctions change.

**WPI (World Port Index)** — a public database of ports, used to detect port calls
and propagate port-related risk.

---

## Infrastructure & method

**H3** — the hexagonal grid we stamp on every location so that "which things are
near each other?" becomes a fast lookup instead of slow geometry. **Res 7** hexes
(~5 km) are the join key; **res 9** (~170 m) are for fine matching. The single most
load-bearing technical decision. (ARCHITECTURE §3, DECISIONS ADR-003.)

**Provenance envelope** — the fixed set of six tracking fields (source, source ref,
observed time, ingest time, code version, confidence) stamped on **every** record.

**DuckDB / Parquet** — Parquet is a compact columnar file format for tables;
DuckDB is a database engine that reads Parquet files directly with no server to run.
Our AIS, detections, tracks, and edges live this way. (DECISIONS ADR-002.)

**Cloudflare R2** — cheap object storage (like Amazon S3 but with no fees for
reading data back out) where we keep large raw SAR scenes. (DECISIONS ADR-010.)

**Connector** — a self-contained module that lands one data source and maps it into
our canonical schema. New source = new connector; the fusion core never learns
source-specific tricks. This is the whole "add paid/classified feeds later without a
rewrite" thesis. (DECISIONS ADR-001.)

**Inspection dashboard** — a deliberately ugly, throwaway visual so the build is
watchable during development. Not the product. The polished product surface is
Phase 6. (CLAUDE.md §6.)

**Systemd service** — the Linux mechanism that keeps the live AIS consumer running
and restarts it after a reboot, so capture never silently stops.

**Cloudflare tunnel** — a way to expose the VM's API to the internet without opening
firewall ports, used to connect the Vercel frontend to the backend.

---

## Terms an operator meets on screen (Phase 6)

**Finding vs candidate** — the distinction the whole sanctions gradient exists to
protect. A **finding** is matched on an IMO (a permanent hull number, hard to
fake) or on a call sign *and* a name agreeing — two independent identifiers. A
**candidate** is a name-only or call-sign-only hit: names change and collide, and
call signs are reassigned by the flag state, so it is a lead to verify rather
than an assertion. A candidate never earns a red treatment and never ranks a row
on the Findings screen. (ADR-018, CLAUDE.md §4.4.)

**Match tier** — *which* of those a given match is: `imo` (0.95) > `call_sign_name`
(0.80) > `call_sign` (0.40) > `name` (0.35). The gap between 0.80 and 0.40 is the
finding threshold and is deliberately wide, so the tiers can never look
interchangeable downstream.

**Designated owner** (as opposed to a sanctions finding) — the sanctions list
named the *company* that owns or operates the hull, not the hull itself. Both are
worth an analyst's time and they are not the same claim, so the badge and the
sentence say which. It also means a vessel name differing from the listed name is
*not* evidence of anything: a ship's name never equals a company's.

**Priority** (Findings screen) — the sum of the named signals a vessel actually
carries, shown with the signals under it. **It is an ordering, not a
probability**, and it is never displayed without its parts. Deliberately not a
blended risk score: a number whose listed reasons do not add up to it is not an
explanation.

**Event density** — per-H3-cell counts of behaviour events, aggregated over the
**whole** corpus rather than over the page of rows the map requested. The plain
event layers are capped and say so; density is the layer that can make a claim
about how much is where. 24,153 loitering events are a solid smear as dots and a
readable surface as graduated hexagons.

**SAR radar contact** — a ship-sized return in a satellite radar image. A contact
drawn **hollow** has no AIS track associated with it. That is the *shape* of a
dark vessel, not a dark vessel: asserting a silence is intentional requires
demonstrated receiver coverage at that position, which mostly does not exist here.
(ADR-005, CLAUDE.md §6.)

**Incident report** — the one-click export: a self-contained HTML file carrying a
vessel's identity and history, why it was flagged, the evidence with its
attribution, the provenance chain, and a **"what this report does not establish"**
section. It opens in any browser and prints to PDF. A scenario vessel's report is
labelled top and bottom and its filename starts `SCENARIO-`, because the label has
to survive the file being forwarded to someone who was not here.
