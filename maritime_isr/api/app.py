"""FastAPI serving layer for the Phase 6 product surface.

Local-only, token-authenticated, CORS-scoped to the dev frontend. Reads the
conformed Parquet tables through DuckDB and the object graph through SQLite; it
writes nothing except alert dispositions (the analyst feedback loop).

Run it with::

    python -m maritime_isr.api            # uvicorn on 127.0.0.1:8000

Every route that carries vessel or edge data returns the provenance envelope and
`is_synthetic`; every count route returns real and synthetic separately. Those
are contract-level guarantees (see :mod:`.models`), not conventions.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import assistant
from . import graph_service as gsvc
from . import models, report, service
from .settings import settings

#: The built frontend, if present. `frontend/npm run build` writes it here; when
#: it exists the API serves the whole UI itself, so the demo needs only Python
#: and one process — no Node, no second server. When it is absent the API is
#: still a pure JSON backend and the frontend runs from the Vite dev server.
DIST_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def require_token(x_api_token: Optional[str] = Header(default=None)) -> None:
    """Shared-secret gate. Simple by design — this serves one laptop (ADR-013)."""
    if x_api_token != settings.token:
        raise HTTPException(status_code=401, detail="invalid or missing API token")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Maritime ISR — Phase 6 API",
        version="0.7.0",
        description="Local serving layer for the Arabian Sea dark-vessel "
                    "fusion prototype. Real and scenario data share every table "
                    "and are always split by is_synthetic (ADR-019).",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    guard = [Depends(require_token)]

    # All JSON routes live under /api so they never collide with the SPA's own
    # client-side routes (/vessels, /alerts, /graph) when the UI is served from
    # this same process.
    api = APIRouter()

    # ---- health (no auth) ------------------------------------------------
    @api.get("/health")
    def health() -> dict:
        return {"status": "ok", "graph": gsvc.graph_exists()}

    # ---- vessels ---------------------------------------------------------
    @api.get("/vessels", dependencies=guard)
    def vessels(
        flag: Optional[str] = None,
        sanctioned: Optional[bool] = None,
        synthetic: Optional[bool] = None,
        min_risk: Optional[float] = Query(default=None, ge=0.0, le=1.0),
        q: Optional[str] = None,
        limit: int = Query(default=500, ge=1, le=settings.max_page),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        res = service.list_vessels(flag=flag, sanctioned=sanctioned,
                                   synthetic=synthetic, min_risk=min_risk,
                                   q=q, limit=limit, offset=offset)
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "total_matched": res["total_matched"],
            "items": [models.VesselSummary(**v).model_dump() for v in res["items"]],
        }

    @api.get("/vessels/{vessel_id}", dependencies=guard)
    def vessel_detail(vessel_id: str) -> dict:
        v = service.get_vessel(vessel_id)
        if v is None:
            raise HTTPException(404, f"no vessel {vessel_id!r}")
        return models.VesselDetail(**v).model_dump()

    @api.get("/vessels/{vessel_id}/track", dependencies=guard)
    def vessel_track(vessel_id: str, start: Optional[str] = None,
                     end: Optional[str] = None,
                     limit: int = Query(default=5000, ge=1, le=50000)) -> dict:
        t = service.get_track(vessel_id, start=start, end=end, limit=limit)
        return models.VesselTrack(**t).model_dump()

    @api.get("/vessels/{vessel_id}/neighbourhood", dependencies=guard)
    def vessel_neighbourhood(vessel_id: str,
                             hops: int = Query(default=1, ge=1, le=2)) -> dict:
        n = gsvc.neighbourhood(vessel_id, hops=hops)
        if n is None:
            raise HTTPException(404, f"{vessel_id!r} is not in the graph")
        return models.Neighbourhood(**n).model_dump()

    # ---- alerts ----------------------------------------------------------
    @api.get("/alerts", dependencies=guard)
    def alerts(synthetic: Optional[bool] = None,
               disposition: Optional[str] = None) -> dict:
        rows = gsvc.list_alerts(is_synthetic=synthetic, disposition=disposition)
        count = {"real": sum(1 for a in rows if not a["is_synthetic"]),
                 "synthetic": sum(1 for a in rows if a["is_synthetic"])}
        return {
            "count": models.SplitCount(**count).model_dump(),
            "items": [models.Alert(**a).model_dump() for a in rows],
        }

    @api.get("/alerts/{alert_id}", dependencies=guard)
    def alert_detail(alert_id: str) -> dict:
        a = gsvc.get_alert(alert_id)
        if a is None:
            raise HTTPException(404, f"no alert {alert_id!r}")
        return models.Alert(**a).model_dump()

    @api.post("/alerts/{alert_id}/disposition", dependencies=guard)
    def dispose(alert_id: str, body: models.Disposition) -> dict:
        if body.disposition not in ("confirm", "dismiss", "watch"):
            raise HTTPException(422, "disposition must be confirm|dismiss|watch")
        ok = gsvc.dispose_alert(alert_id, body.disposition)
        if not ok:
            raise HTTPException(404, f"no alert {alert_id!r}")
        return gsvc.get_alert(alert_id)

    # ---- findings --------------------------------------------------------
    @api.get("/findings", dependencies=guard)
    def findings(synthetic: Optional[bool] = None,
                 limit: int = Query(default=500, ge=1, le=settings.max_page)
                 ) -> dict:
        res = service.list_findings(synthetic=synthetic, limit=limit)
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "total_matched": res["total_matched"],
            "basis_legend": [models.FindingBasis(**b).model_dump()
                             for b in res["basis_legend"]],
            "notes": res["notes"],
            "items": [models.Finding(**f).model_dump() for f in res["items"]],
        }

    @api.get("/vessels/{vessel_id}/report", dependencies=guard)
    def incident_report(vessel_id: str, format: str = Query(
            default="html", pattern="^(html|json)$")):
        """The one-click incident report (CLAUDE.md §0).

        HTML is the default because it is what an operator forwards: it opens
        anywhere, prints to PDF in one keystroke, and is fully self-contained.
        JSON is the same payload for anything that needs to consume it.
        """
        rep = service.build_incident_report(vessel_id)
        if rep is None:
            raise HTTPException(404, f"no vessel {vessel_id!r}")
        if format == "json":
            return rep
        return HTMLResponse(
            report.render_html(rep),
            headers={
                # `attachment` makes the button a download rather than a
                # navigation, and names the file something findable later.
                "Content-Disposition":
                    f'attachment; filename="{report.filename_for(rep)}"',
            })

    # ---- the MDA assistant: ranked Vessels of Interest (ADR-031) ---------
    #
    # Returned as plain dicts rather than through `models`, and deliberately.
    # A factor's `detail` and an evidence item's `detail` are open-shaped by
    # design — a sanctions designation carries a programme and a match tier, a
    # loitering finding carries hours and a zone — so a pydantic model would
    # either flatten every kind to a lowest common denominator or declare
    # `dict` and validate nothing. Same reasoning as `/tracks` above.
    @api.get("/voi", dependencies=guard)
    def voi(synthetic: Optional[bool] = None,
            limit: int = Query(default=50, ge=1, le=settings.max_page),
            min_score: float = Query(default=assistant.MIN_SCORE,
                                     ge=0.0, le=1.0)) -> dict:
        """The ranked Vessel of Interest list — the assistant's front page.

        Rows carry their factors and the points each contributed, but **not**
        the evidence: a fifty-row list with every evidence item attached is
        megabytes, and the evidence belongs on the detail view where somebody
        is actually reading it.

        `suppressed` is not optional decoration. A subject that carried a
        signal and was kept off the list is returned with its reason, so "why
        is this NOT flagged" is answerable from the product rather than only
        from a terminal (the discipline ADR-028 established for the radar
        cascade).
        """
        return assistant.build_list(synthetic=synthetic, limit=limit,
                                    min_score=min_score)

    @api.get("/voi/workload", dependencies=guard)
    def voi_workload() -> dict:
        """Tracks in, subjects out — the workload reduction, measured.

        Declared before `/voi/{subject_id}` so the literal path wins; FastAPI
        matches in declaration order and `workload` would otherwise be captured
        as a subject id.
        """
        return assistant.workload()

    @api.get("/voi/catalog", dependencies=guard)
    def voi_catalog() -> dict:
        """Every factor kind the assistant can rank, and the six families.

        The families with nothing in them are the point: three of the six are
        unbuilt areas of the Section-3 brief, and a surface that lists only
        what it found reads as completeness.
        """
        return {
            "families": assistant.family_coverage(
                list(assistant.FACTOR_KINDS)),
            "kinds": [
                {"kind": s.kind, "label": s.label, "blurb": s.blurb,
                 "family": s.family, "area": s.area, "weight": s.weight,
                 "attribution": s.attribution, "actions": list(s.actions)}
                for s in assistant.FACTOR_KINDS.values()],
        }

    @api.get("/voi/{subject_id:path}", dependencies=guard)
    def voi_detail(subject_id: str) -> dict:
        """One subject in full: every factor, every evidence item, the sum.

        `:path` because a subject id contains colons and may be a contact key
        like `contact:radar:SYN-MUM:0214`.
        """
        v = assistant.build_one(subject_id)
        if v is None:
            raise HTTPException(404, f"no subject {subject_id!r} on the list")
        return v

    @api.post("/voi/{subject_id:path}/ask", dependencies=guard)
    def voi_ask(subject_id: str, body: models.AssistantQuestion) -> dict:
        """Ask a question about one subject, in ordinary language.

        The answer is retrieved, never generated: see `assistant.qa`. Three
        outcomes — `answered`, `no_data` (understood, nothing held) and
        `unsupported` (outside what this system carries, or not understood) —
        and the distinction between the second and third is most of the value.
        """
        a = assistant.ask(subject_id, body.question)
        if a is None:
            raise HTTPException(404, f"no subject {subject_id!r} on the list")
        return a

    # ---- events / scenes / ports ----------------------------------------
    @api.get("/events", dependencies=guard)
    def events(kinds: Optional[str] = Query(default=None,
               description="comma list: encounter,loitering,port_visit,gap"),
               start: Optional[str] = None, end: Optional[str] = None,
               bbox: Optional[str] = Query(default=None,
               description="lon_min,lat_min,lon_max,lat_max"),
               synthetic: Optional[bool] = None,
               limit: int = Query(default=2000, ge=1, le=20000)) -> dict:
        kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
        bbox_t = _parse_bbox(bbox)
        res = service.list_events(kinds=kind_list, start=start, end=end,
                                  bbox=bbox_t, synthetic=synthetic, limit=limit)
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "by_kind": {k: models.SplitCount(**v).model_dump()
                        for k, v in res["by_kind"].items()},
            # Which kinds hit the cap, and their true totals. The map reads this
            # to say "4,000 of 24,153" rather than drawing a prefix in silence.
            "truncated": res["truncated"],
            "note": res["note"],
            "items": [models.Event(**e).model_dump() for e in res["items"]],
        }

    @api.get("/events/density", dependencies=guard)
    def events_density(res: int = Query(default=4, description="H3 resolution: 4, 6 or 7"),
                       kinds: Optional[str] = None,
                       synthetic: Optional[bool] = None) -> dict:
        kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
        try:
            d = service.event_density(res=res, kinds=kind_list,
                                      synthetic=synthetic)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {
            "res": d["res"],
            "count": models.SplitCount(**d["count"]).model_dump(),
            "note": d["note"],
            "items": [models.DensityCell(**c).model_dump() for c in d["items"]],
        }

    @api.get("/detections", dependencies=guard)
    def detections(limit: int = Query(default=5000, ge=1, le=50000)) -> dict:
        d = service.list_detections(limit=limit)
        return {
            "count": models.SplitCount(**d["count"]).model_dump(),
            "note": d["note"],
            "items": [models.Detection(**x).model_dump() for x in d["items"]],
        }

    @api.get("/tracks", dependencies=guard)
    def tracks(max_vessels: int = Query(default=200, ge=1, le=2000),
               max_points: int = Query(default=140, ge=10, le=2000)) -> dict:
        # Decimated AIS tracks for the map's time animation. Returned as a plain
        # dict (compact [lon,lat,epoch] arrays) rather than a per-point model —
        # this can be tens of thousands of points and pydantic-validating each
        # would cost more than it is worth for coordinates.
        return service.list_tracks(max_vessels=max_vessels, max_points=max_points)

    @api.get("/scenes", dependencies=guard)
    def scenes(limit: int = Query(default=2000, ge=1, le=20000)) -> dict:
        res = service.list_scenes(limit=limit)
        return {
            "note": res.get("note"),
            "items": [models.Scene(**s).model_dump() for s in res["items"]],
        }

    @api.get("/ports", dependencies=guard)
    def ports() -> dict:
        res = service.list_ports()
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "items": [models.Port(**p).model_dump() for p in res["items"]],
        }

    # ---- coastal radar (ADR-028) -----------------------------------------
    @api.get("/radar/stations", dependencies=guard)
    def radar_stations() -> dict:
        """The station network, with the size-dependent coverage rings.

        Every row is `is_synthetic: true` and the UI is required to say so on
        the surface. A coverage map is the most persuasive picture this system
        can draw and there is no real radar behind it.
        """
        res = service.list_radar_stations()
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "items": [models.RadarStation(**s).model_dump() for s in res["items"]],
        }

    @api.get("/radar/contacts", dependencies=guard)
    def radar_contacts(limit: int = Query(default=500, ge=1, le=5000),
                       status: Optional[str] = Query(default=None)) -> dict:
        """Dark contacts and their evidence. `status=all` includes suppressions.

        The suppressed rows matter as much as the survivors: "why is this NOT
        flagged" has to be answerable from the product, otherwise the filter
        cascade is a black box the operator has to take on faith.
        """
        res = service.list_radar_contacts(limit=limit, status=status)
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "note": res.get("note"),
            "items": [models.RadarContact(**c).model_dump() for c in res["items"]],
        }

    @api.get("/radar/tracks", dependencies=guard)
    def radar_tracks(max_tracks: int = Query(default=400, ge=1, le=4000),
                     max_points: int = Query(default=60, ge=5, le=1000)) -> dict:
        # Decimated, and returned raw for the same reason as /tracks: compact
        # [lon,lat,epoch] arrays, no per-point pydantic validation.
        return service.list_radar_tracks(max_tracks=max_tracks,
                                         max_points=max_points)

    # ---- the maritime zone layer (ADR-030) -------------------------------
    @api.get("/zones", dependencies=guard)
    def zones(kind: Optional[str] = Query(default=None)) -> dict:
        """The zone layer as GeoJSON, ordered back to front for drawing.

        `missing_kinds` names the statutory limits that are not loaded. A map
        that simply does not draw an EEZ looks identical to one whose EEZ is
        empty, and this system will not derive one — so the gap is reported
        rather than left to be inferred.
        """
        res = service.list_zones([kind] if kind else None)
        return {
            "count": models.SplitCount(**res["count"]).model_dump(),
            "missing_kinds": res["missing_kinds"],
            "note": res.get("note"),
            "items": [models.MaritimeZone(**z).model_dump()
                      for z in res["items"]],
        }

    @api.get("/zones/{zone_id}/vessels", dependencies=guard)
    def zone_vessels(zone_id: str,
                     start: Optional[str] = Query(default=None),
                     end: Optional[str] = Query(default=None),
                     limit: int = Query(default=500, ge=1, le=5000)) -> dict:
        """Who was inside, when, entering from where and leaving to where.

        The sentence this whole layer exists to earn. `basis` is `landed` or
        `none`; `none` means no transitions have been computed for this zone
        yet, which is not the same as nobody having been there.
        """
        res = service.zone_vessels(zone_id, start=start, end=end, limit=limit)
        if res.get("error"):
            raise HTTPException(status_code=404, detail=res["error"])
        return {
            "zone": res["zone"], "basis": res["basis"],
            "n_vessels": res["n_vessels"],
            "count": models.SplitCount(**res["count"]).model_dump(),
            "note": res.get("note"),
            "items": [models.ZoneVisitRow(**v).model_dump()
                      for v in res["items"]],
        }

    @api.post("/geofences", dependencies=guard)
    def create_geofence(req: models.GeofenceRequest) -> dict:
        """Save a drawn area as a zone like any other."""
        try:
            return service.create_geofence(req.name, req.geometry, req.note)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @api.delete("/geofences/{zone_id}", dependencies=guard)
    def delete_geofence(zone_id: str) -> dict:
        """Remove an operator-drawn area. Standing zones are refused."""
        try:
            return service.delete_geofence(zone_id)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    # ---- stats -----------------------------------------------------------
    @api.get("/stats", dependencies=guard)
    def stats() -> dict:
        return models.Stats(**service.get_stats()).model_dump()

    @api.get("/corpus-window", dependencies=guard)
    def corpus_window() -> dict:
        """The corpus time span — a handful of aggregates, not the dashboard.

        The map's time scrubber needs only this, and it used to take it from
        `/stats`, which scans every event table, groups the sanctions matches,
        counts scenes, measures length coverage and walks the graph. The
        scrubber is hidden until its window arrives, so on the real corpus the
        control the demo is built around was the last thing on screen — and it
        vanished again on every navigation away and back, because the view
        remounts and refetches. Splitting the cheap half out is the fix; the
        dashboard keeps using `/stats`.

        Returns FOUR timestamps, not two: `start`/`end` bound the whole corpus,
        `motion_start`/`motion_end` bound the AIS positions. The scrubber plays
        the second pair, because those are the only days on which a vessel can
        move; `note` says so whenever the two differ.
        """
        return service.get_corpus_window()

    @api.get("/graph/all", dependencies=guard)
    def graph_all(limit: int = Query(gsvc.FULL_GRAPH_MAX_NODES, ge=1, le=5000),
                  context: Optional[List[str]] = Query(None)) -> dict:
        """The ownership network as one web, most-connected core first.

        `context` adds a family of context relationships back — `flag`, `port`
        or `identity`, repeatable. They are off by default because they are
        true of nearly every vessel and are what turns the picture into a
        hairball: on the fixture graph they were 88% of the edges, and the
        single `flag:IND` node joined 156 hulls.

        `truncated`, `total_nodes` and `total_edges` are not optional decoration
        — the real corpus graph is an estimated ~19,000 nodes and this returns
        at most 1,500 of them, so a caller that ignores those fields will draw
        a partial picture and present it as complete. `matched_*` separates
        what a filter hid from what the limit cut.
        """
        return gsvc.full_graph(limit, context=context)

    @api.get("/graph/seeds", dependencies=guard)
    def graph_seeds(limit: int = Query(12, ge=1, le=100)) -> dict:
        """Vessels worth opening the graph on, most-connected first.

        `degree` travels with each one because on this corpus "best available"
        and "well connected" are not the same thing — GFW ownership covers
        ~1.3% of hulls, so the top of this list can still be a small cluster.
        """
        items = gsvc.best_seeds(limit)
        return {"items": items, "count": len(items)}

    app.include_router(api, prefix="/api")
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built frontend from this process when it has been built.

    This is what makes the demo Python-only: no Node, no Vite dev server, one
    command. The built assets are served from `frontend/dist/assets`, and every
    other path falls back to `index.html` so the client-side router (which owns
    /vessels, /alerts, /graph, /vessels/:id) works on a hard refresh or a pasted
    link. When `dist/` is absent this is a no-op and the API stays a pure JSON
    backend for the Vite dev server to proxy.
    """
    if not (DIST_DIR / "index.html").exists():
        return

    assets = DIST_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    def index_html() -> str:
        """Read index.html per request, not once at startup.

        Vite writes content-hashed bundle names, so a rebuild changes the script
        tag inside index.html. Caching the old copy leaves the page pointing at a
        filename that no longer exists — the browser 404s on the bundle and
        renders a blank white screen, with the server reporting nothing wrong.
        The file is a few hundred bytes, so re-reading it costs nothing and means
        a rebuild is picked up without restarting the API.

        The token is injected here so a non-default MISR_API_TOKEN still works
        when the UI is served from this process rather than proxied by Vite. On
        localhost the token is a convenience, not a boundary (ADR-013).
        """
        raw = (DIST_DIR / "index.html").read_text(encoding="utf-8")
        return raw.replace(
            "</head>",
            f'<script>window.__MISR_TOKEN__="{settings.token}";</script></head>')

    @app.get("/", response_class=HTMLResponse)
    def _index() -> HTMLResponse:
        return HTMLResponse(index_html())

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def _spa(full_path: str):
        # A real file under dist (favicon, etc.) is served as-is; anything else
        # is a client-side route and gets index.html.
        candidate = DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return HTMLResponse(index_html())


def _parse_bbox(bbox: Optional[str]) -> Optional[tuple]:
    if not bbox:
        return None
    try:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        return tuple(parts)
    except ValueError:
        raise HTTPException(422, "bbox must be lon_min,lat_min,lon_max,lat_max")


app = create_app()
