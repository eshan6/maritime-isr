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
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

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

    # ---- stats -----------------------------------------------------------
    @api.get("/stats", dependencies=guard)
    def stats() -> dict:
        return models.Stats(**service.get_stats()).model_dump()

    @api.get("/corpus-window", dependencies=guard)
    def corpus_window() -> dict:
        """Just the corpus time span — two aggregates, not the whole dashboard.

        The map's time scrubber needs only this, and it used to take it from
        `/stats`, which scans every event table, groups the sanctions matches,
        counts scenes, measures length coverage and walks the graph. The
        scrubber is hidden until its window arrives, so on the real corpus the
        control the demo is built around was the last thing on screen — and it
        vanished again on every navigation away and back, because the view
        remounts and refetches. Splitting the cheap half out is the fix; the
        dashboard keeps using `/stats`.
        """
        return service.get_corpus_window()

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
