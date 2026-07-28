"""Shared HTTP client for the Global Fishing Watch API v3.

One place that knows about the base URL, the bearer token, retry/backoff, and
offset pagination, so each GFW connector only has to describe *what* it wants.

Endpoint shapes here were read from the official GFW Python client source
(github.com/GlobalFishingWatch/gfw-api-python-client), because the rendered API
docs return HTTP 403 to automated fetches. See DATA_SOURCES.md.

Rate limits are generous relative to our use — 50,000 requests/day — so the
retry policy is deliberately patient rather than aggressive.
"""
from __future__ import annotations

import os
import time
from typing import Any, Iterator

import requests

API_BASE = "https://gateway.api.globalfishingwatch.org/v3"

# GFW returns at most this many rows per events request; we page with offset.
PAGE_SIZE = 1000
MAX_PAGES = 500          # hard stop so a bad filter cannot loop forever
TIMEOUT_S = 120
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_RETRIES = 5


class GFWAuthError(RuntimeError):
    """No usable API token. Raised early with a plain-English fix."""


class GFWUnavailable(RuntimeError):
    """The endpoint or dataset is temporarily unavailable (e.g. the SAR outage)."""


def token() -> str:
    tok = os.getenv("GFW_API_TOKEN")
    if not tok:
        raise GFWAuthError(
            "GFW_API_TOKEN is not set.\n"
            "  1. Register (free) at https://globalfishingwatch.org/our-apis/\n"
            "  2. Generate an API token.\n"
            "  3. Put GFW_API_TOKEN=<your token> in a .env file at the repo root.\n"
            "Then run `maritime-isr doctor` to confirm it is picked up."
        )
    return tok


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def request(method: str, path: str, *, params: dict | None = None,
            json_body: dict | None = None) -> requests.Response:
    """One GFW call with retry/backoff on transient failures."""
    url = f"{API_BASE}/{path.lstrip('/')}"
    delay = 2.0
    last: requests.Response | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.request(
            method, url, headers=_headers(), params=params, json=json_body,
            timeout=TIMEOUT_S,
        )
        last = resp
        if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
            wait = float(resp.headers.get("Retry-After", delay))
            print(f"[gfw] HTTP {resp.status_code} on {path}; retry {attempt}/{MAX_RETRIES} "
                  f"in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2
            continue
        break

    assert last is not None
    if last.status_code == 401:
        raise GFWAuthError(
            "GFW rejected the token (HTTP 401). It may be expired or mistyped. "
            "Regenerate it at https://globalfishingwatch.org/our-apis/"
        )
    if last.status_code == 403:
        raise GFWAuthError(
            "GFW returned HTTP 403 — the token is valid but not authorised for "
            "this dataset. Check the dataset is included in your API access tier."
        )
    return last


def post_paginated(path: str, body: dict, *, limit: int = PAGE_SIZE) -> Iterator[dict]:
    """Yield every entry from an offset-paginated POST endpoint.

    GFW's events endpoint returns {"entries": [...], "total": N, ...}. We keep
    requesting until a short page comes back or we run out of page budget.
    """
    offset = 0
    for page in range(MAX_PAGES):
        resp = request("POST", path, params={"limit": limit, "offset": offset}, json_body=body)

        if resp.status_code >= 400:
            snippet = resp.text[:300]
            if resp.status_code in (404, 422):
                raise GFWUnavailable(
                    f"GFW {path} returned HTTP {resp.status_code}. The dataset id or "
                    f"filter may have changed. Response: {snippet}"
                )
            resp.raise_for_status()

        payload = resp.json()
        entries = payload.get("entries") or payload.get("data") or []
        if not entries:
            return
        for e in entries:
            yield e
        if len(entries) < limit:
            return
        offset += len(entries)

    print(f"[gfw] WARNING: hit MAX_PAGES ({MAX_PAGES}) on {path} — results may be truncated")


def get_json(path: str, params: dict | None = None) -> Any:
    resp = request("GET", path, params=params)
    if resp.status_code >= 400:
        resp.raise_for_status()
    return resp.json()


def aoi_geojson(aoi) -> dict:
    """Our AOI as the GeoJSON polygon GFW's `geometry` filter expects."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [aoi.lon_min, aoi.lat_min],
            [aoi.lon_max, aoi.lat_min],
            [aoi.lon_max, aoi.lat_max],
            [aoi.lon_min, aoi.lat_max],
            [aoi.lon_min, aoi.lat_min],
        ]],
    }
