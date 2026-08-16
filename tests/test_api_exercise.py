"""Exercise tests for the Phase 6 API.

These are **not** existence checks. Every test hits an endpoint against a real
DuckDB/Parquet corpus and asserts the query actually returned non-empty,
correctly-shaped data — because in this codebase a check that a table *exists* or
a response *has a key* has repeatedly passed while the thing underneath was
broken (STATE.md's host-only bug list; ADR-021).

**Where the corpus comes from.** The tests run against whatever is landed under
`cfg.data_root`: the operator's real corpus on the laptop, or the generated
scenario corpus in the sandbox (`python -m maritime_isr.cli scenario generate`
then `python tools/run_scenario_pipeline.py`). If neither is present the module
skips with instructions rather than failing — the existing suite stays green on a
bare checkout.

**Isolation.** A session fixture copies the whole data root to a temp directory
and points `cfg.data_root` at the copy, so the disposition test can write to the
graph without touching the operator's real data.
"""
from __future__ import annotations

import shutil

import pytest

from maritime_isr.config import cfg

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module, pristine_corpus):
    if pristine_corpus is None:
        pytest.skip(
            "no landed corpus — run `python -m maritime_isr.cli scenario "
            "generate` and `python tools/run_scenario_pipeline.py` first")
    # restore the pristine snapshot into our own temp root, so writes
    # (dispositions) are contained and independent of any other test's mutations
    dst = tmp_path_factory.mktemp("misr_data")
    for name in ("conformed", "misr.duckdb", "graph.sqlite"):
        s = pristine_corpus / name
        if s.is_dir():
            shutil.copytree(s, dst / name)
        elif s.exists():
            shutil.copy2(s, dst / name)
    monkeypatch_module.setattr(cfg, "data_root", dst)

    from maritime_isr.api import graph_service
    graph_service._risk_cache["mtime"] = None  # force recompute against the copy

    from maritime_isr.api.app import create_app
    from fastapi.testclient import TestClient
    return TestClient(create_app())


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def H():
    from maritime_isr.api.settings import settings
    return {"X-API-Token": settings.token}


@pytest.fixture(scope="module")
def alerts_or_skip(client, H):
    """The alert list, or a skip if the graph is unpopulated.

    Alerts come from the anomaly library over the full track-engine pipeline
    (`tools/run_scenario_pipeline.py`), not from the conformed tables, and the
    graph is easily depleted by other tests that open the default-path graph.
    When it is empty there is nothing to exercise, so these tests skip with
    instructions rather than fail — the same posture the module takes when no
    corpus is landed at all."""
    items = client.get("/api/alerts", headers=H).json()["items"]
    if not items:
        pytest.skip("graph has no alerts — run "
                    "`python tools/run_scenario_pipeline.py` to populate it")
    return items


# --------------------------------------------------------------------------
# schema robustness — the real corpus has tables without is_synthetic
# --------------------------------------------------------------------------

def test_split_tolerates_table_without_is_synthetic():
    """The real `sanctioned_vessel_matches` has no is_synthetic column; a split
    over it must count every row as real, not 500. Regression for the live crash
    on 2026-08-01. Needs no corpus — builds its own table."""
    import duckdb

    from maritime_isr.api import service
    from maritime_isr.api.reader import Reader

    con = duckdb.connect()
    con.execute("CREATE TABLE sanctioned_vessel_matches "
                "(vessel_id VARCHAR, is_finding BOOLEAN)")
    con.execute("INSERT INTO sanctioned_vessel_matches VALUES "
                "('a', true), ('b', false), ('c', true)")
    r = Reader(con)
    assert service._split(r, "sanctioned_vessel_matches") == {"real": 3, "synthetic": 0}
    con.close()


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def test_requires_token(client):
    assert client.get("/api/vessels").status_code == 401
    assert client.get("/api/health").status_code == 200  # health is open


# --------------------------------------------------------------------------
# stats — the split-count contract (ADR-019)
# --------------------------------------------------------------------------

def test_stats_splits_real_and_synthetic(client, H):
    s = client.get("/api/stats", headers=H).json()
    for key in ("vessels", "alerts", "sanctions_matches", "graph_nodes",
                "graph_edges"):
        assert set(s[key]) == {"real", "synthetic"}, f"{key} must be split only"
        assert "total" not in s[key], "counts must never carry a blended total"
    # a landed corpus has vessels
    assert s["vessels"]["real"] + s["vessels"]["synthetic"] > 0
    # events are split per kind
    assert set(s["events"]) == {"encounter", "loitering", "port_visit", "gap"}
    for k, v in s["events"].items():
        assert set(v) == {"real", "synthetic"}
    assert s["corpus_window"]["start"] and s["corpus_window"]["end"]


# --------------------------------------------------------------------------
# vessels
# --------------------------------------------------------------------------

def test_vessels_list_shaped_and_provenanced(client, H):
    r = client.get("/api/vessels?limit=25", headers=H).json()
    assert r["items"], "a landed corpus must return vessels"
    assert set(r["count"]) == {"real", "synthetic"}
    for v in r["items"]:
        assert v["id"].startswith("vessel:")
        assert "is_synthetic" in v
        # provenance envelope present on every vessel row (CLAUDE.md §4.1)
        assert set(v["prov"]) >= {"source_id", "acquired_at", "ingested_at",
                                  "pipeline_version"}
    # sorted by risk descending
    risks = [v["risk_score"] or 0.0 for v in r["items"]]
    assert risks == sorted(risks, reverse=True)


def test_vessels_filter_synthetic(client, H):
    syn = client.get("/api/vessels?synthetic=true&limit=500", headers=H).json()
    assert syn["items"]
    assert all(v["is_synthetic"] for v in syn["items"])


def test_vessel_detail_has_history_and_decomposed_risk(client, H):
    vid = client.get("/api/vessels?limit=1", headers=H).json()["items"][0]["id"]
    d = client.get(f"/api/vessels/{vid}", headers=H).json()
    assert d["current"]["name"] is not None or d["current"]["mmsi"] is not None
    assert len(d["identity_history"]) >= 1
    for iv in d["identity_history"]:
        assert "valid_from" in iv and "is_synthetic" in iv
    if d["risk"]:
        # the explainability contract: score == sum of weighted components
        got = d["risk"]["risk_score"]
        summed = sum(c["weighted"] for c in d["risk"]["components"].values())
        assert abs(got - summed) < 1e-3, "risk must equal the sum of its parts"


def test_sanctioned_vessel_shows_tier_not_bare_flag(client, H):
    # find a vessel with a sanctions match, if any exist in this corpus
    vessels = client.get("/api/vessels?sanctioned=true&limit=50", headers=H).json()
    if not vessels["items"]:
        pytest.skip("no sanctions matches in this corpus")
    vid = vessels["items"][0]["id"]
    d = client.get(f"/api/vessels/{vid}", headers=H).json()
    assert d["sanctions"], "a sanctioned vessel must carry its match rows"
    for m in d["sanctions"]:
        assert m["match_tier"] is not None
        # a name-only candidate must never be marked a finding (ADR-018)
        if m["match_tier"] == "name":
            assert m["is_finding"] is False


def test_missing_vessel_404(client, H):
    assert client.get("/api/vessels/vessel:gfw:does-not-exist", headers=H).status_code == 404


# --------------------------------------------------------------------------
# track
# --------------------------------------------------------------------------

def test_some_vessel_has_a_track(client, H):
    # scan a handful — offshore vessels legitimately have no AIS (ADR-005)
    vids = [v["id"] for v in
            client.get("/api/vessels?limit=40", headers=H).json()["items"]]
    found = False
    for vid in vids:
        t = client.get(f"/api/vessels/{vid}/track", headers=H).json()
        assert "points" in t and "note" in t
        if t["points"]:
            p = t["points"][0]
            assert {"ts", "lat", "lon"} <= set(p)
            found = True
            break
    assert found, "at least one vessel must have AIS positions"


# --------------------------------------------------------------------------
# neighbourhood
# --------------------------------------------------------------------------

def test_neighbourhood_is_bounded_and_acyclic(client, H):
    # pick a vessel that is actually in the graph
    vids = [v["id"] for v in
            client.get("/api/vessels?limit=40", headers=H).json()["items"]]
    nb = None
    for vid in vids:
        r = client.get(f"/api/vessels/{vid}/neighbourhood?hops=1", headers=H)
        if r.status_code == 200 and r.json()["edges"]:
            nb = r.json()
            break
    if not nb:
        pytest.skip("graph unpopulated — run tools/run_scenario_pipeline.py")
    ids = [n["id"] for n in nb["nodes"]]
    assert len(ids) == len(set(ids)), "nodes must be de-duplicated"
    assert len(nb["nodes"]) <= nb["budget"]
    node_ids = set(ids)
    for e in nb["edges"]:
        assert e["source"] in node_ids and e["target"] in node_ids
        assert {"edge_type", "confidence", "t_start"} <= set(e)


def test_neighbourhood_two_hops_expands(client, H):
    vid = None
    for v in client.get("/api/vessels?limit=40", headers=H).json()["items"]:
        r1 = client.get(f"/api/vessels/{v['id']}/neighbourhood?hops=1", headers=H)
        if r1.status_code == 200 and r1.json()["edges"]:
            vid = v["id"]
            n1 = r1.json()
            break
    if not vid:
        pytest.skip("no connected vessel")
    n2 = client.get(f"/api/vessels/{vid}/neighbourhood?hops=2", headers=H).json()
    assert len(n2["nodes"]) >= len(n1["nodes"])


# --------------------------------------------------------------------------
# alerts + disposition (the feedback loop)
# --------------------------------------------------------------------------

def test_alerts_carry_evidence_chains(client, H, alerts_or_skip):
    a = client.get("/api/alerts", headers=H).json()
    assert set(a["count"]) == {"real", "synthetic"}
    for al in alerts_or_skip:
        # **Not every alert is about a vessel, and that is deliberate.** A
        # dark-vessel alert names the *contact* — a SAR detection, or a coastal
        # radar track (ADR-028) — because nothing is known about which hull it
        # is. Naming a vessel there would be inventing one, and the whole point
        # of the finding is that it cannot be named. What must hold for every
        # alert is that its subject is a kind of thing the graph models and that
        # it renders an evidence chain.
        assert al["subject"].startswith(("vessel:", "detection:", "contact:")), (
            f"alert subject {al['subject']!r} is not a modelled node kind")
        assert al["evidence"], "every alert renders an evidence chain"
        assert "is_synthetic" in al


def test_alert_detail_matches_list(client, H, alerts_or_skip):
    first = alerts_or_skip[0]
    d = client.get(f"/api/alerts/{first['id']}", headers=H).json()
    assert d["id"] == first["id"]
    assert d["anomaly_type"] == first["anomaly_type"]


def test_disposition_persists(client, H, alerts_or_skip):
    aid = alerts_or_skip[0]["id"]
    r = client.post(f"/api/alerts/{aid}/disposition", headers=H,
                    json={"alert_id": aid, "disposition": "watch"})
    assert r.status_code == 200
    assert r.json()["disposition"] == "watch"
    # re-read from a fresh query proves it was written, not just echoed
    again = client.get(f"/api/alerts/{aid}", headers=H).json()
    assert again["disposition"] == "watch"


def test_bad_disposition_rejected(client, H, alerts_or_skip):
    aid = alerts_or_skip[0]["id"]
    r = client.post(f"/api/alerts/{aid}/disposition", headers=H,
                    json={"alert_id": aid, "disposition": "banana"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# events / scenes / ports
# --------------------------------------------------------------------------

def test_events_split_by_kind_and_filter_by_bbox(client, H):
    ev = client.get("/api/events?limit=5000", headers=H).json()
    assert ev["items"], "the corpus has events"
    assert set(ev["count"]) == {"real", "synthetic"}
    for e in ev["items"]:
        assert e["kind"] in ("encounter", "loitering", "port_visit", "gap")
        assert "is_synthetic" in e and "prov" in e
        assert e["attribution"], "events must attribute their source"
    # a gap event, if present, must carry a GFW-attributed classification
    for e in ev["items"]:
        if e["kind"] == "gap":
            assert e["classification"] is not None
    # bbox filter is a real filter, not a no-op
    full = ev["count"]["real"] + ev["count"]["synthetic"]
    tiny = client.get("/api/events?bbox=60,5,61,6&limit=5000", headers=H).json()
    tiny_n = tiny["count"]["real"] + tiny["count"]["synthetic"]
    assert tiny_n <= full


def test_tracks_shape_for_animation(client, H):
    r = client.get("/api/tracks", headers=H).json()
    assert "items" in r
    if not r["items"]:
        pytest.skip("no AIS tracks in this corpus (real corpus has no free AIS)")
    tr = r["items"][0]
    assert tr["vessel_id"].startswith("vessel:")
    assert "is_synthetic" in tr
    assert len(tr["points"]) >= 2
    # each point is [lon, lat, epoch_seconds] — what the map interpolates on
    lon, lat, epoch = tr["points"][0]
    assert 60 <= lon <= 78 and 5 <= lat <= 25  # inside the AOI
    assert isinstance(epoch, int) and epoch > 0
    # points are time-ordered so interpolation is monotonic
    ts = [p[2] for p in tr["points"]]
    assert ts == sorted(ts)


def test_scenes_shape(client, H):
    sc = client.get("/api/scenes", headers=H).json()
    assert "items" in sc
    for s in sc["items"]:
        assert s["scene_id"] and "prov" in s


def test_ports_non_empty_and_split(client, H):
    po = client.get("/api/ports", headers=H).json()
    assert po["items"], "the gazetteer must have ports"
    assert set(po["count"]) == {"real", "synthetic"}
    for p in po["items"]:
        assert {"id", "name", "source", "is_synthetic"} <= set(p)


# ==========================================================================
# findings — the ranked table
#
# The screen exists because `graph_report.py` measured that a network view has
# nothing to draw on the real corpus. These tests protect the two properties
# that make it trustworthy rather than merely populated: a row never appears
# without a stated basis, and a determination never appears without the name of
# whoever made it.
# ==========================================================================

def _findings_or_skip(client, H):
    d = client.get("/api/findings", headers=H).json()
    if not d["items"]:
        pytest.skip("no findings in this corpus — run the sanctions matcher")
    return d


def test_findings_returns_ranked_rows_with_split_counts(client, H):
    d = _findings_or_skip(client, H)
    assert d["count"]["real"] + d["count"]["synthetic"] == d["total_matched"]
    prios = [f["priority"] for f in d["items"]]
    assert prios == sorted(prios, reverse=True), "highest priority first"


def test_every_finding_states_why_it_is_there(client, H):
    """A row with no basis is an unexplained alert, which is the thing this
    product cannot ship (CLAUDE.md §4.1)."""
    for f in _findings_or_skip(client, H)["items"]:
        assert f["basis"], f"{f['id']} ranks with no stated signal"
        assert f["priority"] == sum(b["weight"] for b in f["basis"]), (
            "priority must be exactly the sum of the signals shown — a number "
            "the listed reasons do not add up to is not an explanation")


def test_every_finding_names_who_determined_it(client, H):
    """GFW assessed the gaps and OFAC/UN/EU decided the designations; ours is
    the identity match. A row that does not say so invites 'we detected a dark
    vessel' (CLAUDE.md §6)."""
    for f in _findings_or_skip(client, H)["items"]:
        assert f["attribution"], f"{f['id']} carries no attribution"
        for g in f["dark_gaps"]:
            assert "Global Fishing Watch" in g["attribution"]


def test_a_gap_finding_is_never_described_as_our_detection(client, H):
    for f in _findings_or_skip(client, H)["items"]:
        head = f["headline"].lower()
        assert "we detected" not in head
        if f["has_dark_gap"]:
            assert "global fishing watch" in head, (
                "a dark-gap headline must name GFW as the assessor")


def test_findings_notes_label_the_synthetic_split(client, H):
    d = _findings_or_skip(client, H)
    joined = " ".join(d["notes"]).lower()
    assert "scenario" in joined and "real" in joined


def test_a_name_only_candidate_never_becomes_a_finding_row(client, H):
    """Candidates are leads for the vessels table. Promoting them here is the
    alert-fatigue failure ADR-004 names outright."""
    for f in _findings_or_skip(client, H)["items"]:
        if not f["has_dark_gap"]:
            assert any(s["is_finding"] for s in f["sanctions"]), (
                f"{f['id']} is listed on candidate-grade evidence alone")


def test_an_organisation_designation_is_not_reported_as_a_listed_hull(client, H):
    """`ofac_name` holds a vessel name for a real matcher row and a company
    name for a scenario ownership match. Comparing the two is what made every
    scenario row claim identity laundering."""
    for f in _findings_or_skip(client, H)["items"]:
        org_only = f["sanctions"] and all(
            s.get("listed_entity_type") == "organisation"
            for s in f["sanctions"] if s["is_finding"])
        if org_only and f["sanctions_is_finding"]:
            assert "not listed" in f["headline"], (
                "the headline must say the hull itself is not designated")
            assert not any(b["signal"] == "name_disagreement"
                           for b in f["basis"]), (
                "a hull name differing from a company name is not a name "
                "disagreement")


def test_findings_never_expose_scenario_truth(client, H):
    """The answer key is forbidden to every serving path (ADR-019 §d)."""
    body = client.get("/api/findings", headers=H).text.lower()
    for leak in ("scenario_truth", "expected_anomaly", "true_anomaly",
                 "deliberate_miss", "decoy"):
        assert leak not in body, f"{leak} leaked into the findings response"


# ==========================================================================
# event density + truncation honesty
# ==========================================================================

def test_density_counts_the_whole_corpus_not_a_page(client, H):
    """The number this endpoint exists to fix.

    The map used to request N events and draw them. On the real corpus that
    silently truncated 24,153 loitering events to the earliest 4,000, so the
    map showed a chronological prefix and stopped. Density is aggregated
    server-side over every row, so its total must be >= any capped page.
    """
    d = client.get("/api/events/density", params={"res": 4}, headers=H).json()
    if not d["items"]:
        pytest.skip("no positioned events in this corpus")
    dens_total = d["count"]["real"] + d["count"]["synthetic"]
    page = client.get("/api/events", params={"limit": 5}, headers=H).json()
    page_total = page["count"]["real"] + page["count"]["synthetic"]
    assert dens_total > page_total, (
        f"density {dens_total} must exceed a 5-per-kind page {page_total}")


def test_density_rejects_a_resolution_it_cannot_serve(client, H):
    assert client.get("/api/events/density", params={"res": 9},
                      headers=H).status_code == 422


def test_density_cells_carry_a_position_and_a_kind_breakdown(client, H):
    d = client.get("/api/events/density", params={"res": 4}, headers=H).json()
    if not d["items"]:
        pytest.skip("no positioned events in this corpus")
    for c in d["items"][:10]:
        assert c["lat"] is not None and c["lon"] is not None
        assert c["by_kind"], "a cell with no kind breakdown cannot be read"
        assert sum(c["by_kind"].values()) == c["real"] + c["synthetic"]


def test_a_truncated_event_response_says_so(client, H):
    """Silent truncation is the defect; the loud version is the fix."""
    d = client.get("/api/events", params={"limit": 1}, headers=H).json()
    if not d["items"]:
        pytest.skip("no events in this corpus")
    assert d["truncated"], "a 1-row page over a real table must report truncation"
    assert "TRUNCATED" in (d["note"] or "")
    for kind, t in d["truncated"].items():
        assert t["matching"] > t["returned"], kind


def test_an_uncapped_event_response_reports_no_truncation(client, H):
    d = client.get("/api/events", params={"limit": 20000}, headers=H).json()
    assert d["truncated"] == {}
    assert d["note"] is None


# ==========================================================================
# SAR detections
# ==========================================================================

def test_detections_shape_and_synthetic_labelling(client, H):
    d = client.get("/api/detections", headers=H).json()
    if not d["items"]:
        pytest.skip("no detection table landed")
    for x in d["items"][:10]:
        assert x["lat"] is not None and x["lon"] is not None
        assert x["prov"]["source_id"], "a contact with no provenance cannot land"


def test_synthetic_only_detections_say_no_real_sar_was_processed(client, H):
    """An empty real split must not read as 'the pipeline ran and found
    nothing' — no SAR scene has been processed at all (ADR-017)."""
    d = client.get("/api/detections", headers=H).json()
    if not d["items"]:
        pytest.skip("no detection table landed")
    if d["count"]["real"] == 0 and d["count"]["synthetic"] > 0:
        assert "synthetic" in (d["note"] or "").lower()
        assert "dark" in (d["note"] or "").lower(), (
            "the note must withhold the word 'dark' from an unmatched contact")


# ==========================================================================
# the one-click incident report
#
# CLAUDE.md §0 defines done as: find a dark vessel, click it, read the reason,
# and EXPORT A ONE-CLICK INCIDENT REPORT. This file is the thing that leaves
# the building and gets forwarded to someone who was not here, so the tests
# below are mostly about what it must not be able to imply once it has.
# ==========================================================================

def _any_vessel(client, H):
    items = client.get("/api/vessels", params={"limit": 1}, headers=H).json()["items"]
    if not items:
        pytest.skip("no vessels in this corpus")
    return items[0]["id"]


def _report(client, H, vid=None, fmt=None):
    vid = vid or _any_vessel(client, H)
    params = {"format": fmt} if fmt else None
    return client.get(f"/api/vessels/{vid}/report", params=params, headers=H)


def test_report_downloads_as_a_named_html_file(client, H):
    r = _report(client, H)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    disp = r.headers.get("content-disposition", "")
    assert "attachment" in disp and ".html" in disp, (
        "the export must download rather than navigate")


def test_report_is_self_contained(client, H):
    """It has to say the same thing on a laptop with no network. A report that
    silently loses its stylesheet or an image when forwarded is a report that
    can be misread."""
    body = _report(client, H).text
    for pattern in ('src="http', "src='http", 'href="http', "href='http",
                    "@import", "url(http"):
        assert pattern not in body, f"external reference {pattern!r} in the report"


def test_report_404s_for_a_vessel_that_does_not_exist(client, H):
    """A dossier about nothing is worse than no dossier."""
    assert client.get("/api/vessels/vessel:gfw:no-such-hull/report",
                      headers=H).status_code == 404


def test_a_synthetic_vessel_is_unmistakably_labelled(client, H):
    """The label has to survive being forwarded, because by then it is out of
    our hands (CLAUDE.md §4.6)."""
    syn = client.get("/api/vessels", params={"synthetic": True, "limit": 1},
                     headers=H).json()["items"]
    if not syn:
        pytest.skip("no synthetic vessels in this corpus")
    r = _report(client, H, syn[0]["id"])
    body = r.text
    assert "NOT A REAL VESSEL" in body
    assert body.count("SCENARIO DATA") >= 2, (
        "the banner must appear at the top AND the bottom — a long report is "
        "read from wherever it was scrolled to")
    assert "SCENARIO-" in r.headers.get("content-disposition", ""), (
        "the filename itself must carry the label")


def test_a_report_never_claims_we_detected_a_dark_vessel(client, H):
    """The overclaim that would travel furthest (CLAUDE.md §5, §6)."""
    for vid in [v["id"] for v in client.get(
            "/api/vessels", params={"limit": 8}, headers=H).json()["items"]]:
        body = _report(client, H, vid).text.lower()
        for phrase in ("we detected a dark vessel", "dark vessel detected",
                       "confirmed dark vessel"):
            assert phrase not in body, f"{vid} report contains {phrase!r}"


def test_every_report_states_what_it_does_not_establish(client, H):
    """An omission reads as 'no concern here' unless it is named as 'we cannot
    see this'."""
    body = _report(client, H).text
    assert "does not establish" in body.lower()
    assert "did not detect a dark vessel" in body.lower()


def test_a_gap_assessment_in_a_report_is_attributed_to_gfw(client, H):
    """If any vessel in this corpus carries a GFW-flagged gap, its report must
    name GFW as the assessor rather than presenting it as our finding."""
    findings = client.get("/api/findings", headers=H).json()["items"]
    dark = [f for f in findings if f["has_dark_gap"]]
    if not dark:
        pytest.skip("no GFW-flagged gaps in this corpus")
    body = _report(client, H, dark[0]["id"]).text
    assert "Global Fishing Watch" in body
    assert "did not compute" in body, (
        "the report must disclaim authorship of GFW's assessment")


def test_a_sanctions_candidate_is_never_rendered_as_a_finding(client, H):
    """ADR-018's gradient has to survive into the exported document, which is
    the copy that outlives the screen."""
    findings = client.get("/api/findings", headers=H).json()["items"]
    with_cand = [f for f in findings
                 if any(not s["is_finding"] for s in f["sanctions"])]
    if not with_cand:
        pytest.skip("no sanctions candidates in this corpus")
    body = _report(client, H, with_cand[0]["id"]).text
    assert "candidate" in body
    assert "lead to verify" in body


def test_json_and_html_are_the_same_payload(client, H):
    """Two renderings of one dict — so a caveat added in one cannot go missing
    from the other."""
    vid = _any_vessel(client, H)
    j = _report(client, H, vid, fmt="json").json()
    assert set(j) >= {"vessel", "finding", "alerts", "summary",
                      "not_established", "provenance", "is_synthetic"}
    html = _report(client, H, vid).text
    for line in j["not_established"]:
        # the HTML escapes, so compare on a distinctive unescaped fragment
        frag = line.split(".")[0][:40]
        assert frag.replace("'", "&#x27;") in html or frag in html


def test_the_report_carries_its_provenance(client, H):
    """A finding an analyst cannot trace to a source and a code version is
    worthless — worse, a landmine (CLAUDE.md §4.1)."""
    j = _report(client, H, fmt="json").json()
    assert j["provenance"]["sources"], "no source id reached the report"
    body = _report(client, H).text
    assert "Provenance" in body


def test_a_report_can_be_exported_for_a_vessel_with_no_finding(client, H):
    """An analyst establishing whether a hull is worth flagging needs to hand
    over what is known about it. Refusing unless it is already flagged makes
    the export useless in exactly that case."""
    findings = {f["id"] for f in
                client.get("/api/findings", headers=H).json()["items"]}
    plain = [v["id"] for v in client.get("/api/vessels", params={"limit": 200},
                                         headers=H).json()["items"]
             if v["id"] not in findings]
    if not plain:
        pytest.skip("every vessel in this corpus carries a finding")
    body = _report(client, H, plain[0]).text
    assert "carries no finding" in body
