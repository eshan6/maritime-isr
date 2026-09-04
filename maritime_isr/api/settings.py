"""API settings — local-only serving, token auth, dev-frontend CORS.

This began as a demo surface for a laptop. It is now also served publicly from
a Hugging Face Docker Space, which changes what these defaults mean and does
not change what they *do*: the token still ships to the browser, so it protects
nothing from anyone who opens the page.

The deploy is acceptable only because the corpus is entirely synthetic. There
is still no multi-tenant auth here, and a real feed would need one built first.

CORS is opened only to the local dev frontend origins, and the public deploy
does not widen it: Vercel proxies `/api` server-side, so the browser sees one
origin and never issues a cross-origin request at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Default dev token. Overridden by MISR_API_TOKEN. A default exists so the demo
#: runs out of the box on the laptop.
#:
#: **It is not a security boundary, and since the Hugging Face deploy it is no
#: longer true that this only runs on localhost.** The browser has to send this
#: token, so it ships inside the page the API serves — a token the client must
#: know is a token the public knows. Changing it does not make a deployed Space
#: private; it only stops a stale bookmark working.
#:
#: What actually makes the deploy acceptable is that every row it serves is
#: synthetic (CLAUDE.md §4.6). Do not put a real feed behind this without
#: building real auth first.
DEFAULT_TOKEN = "maritime-isr-dev"

#: Origins the Vite/React dev server runs on. Kept explicit rather than "*" so a
#: stray page on the machine cannot call the API with the browser's credentials.
DEFAULT_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


@dataclass(frozen=True)
class ApiSettings:
    token: str = field(default_factory=lambda: os.getenv("MISR_API_TOKEN", DEFAULT_TOKEN))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip() for o in os.getenv("MISR_API_CORS", "").split(",") if o.strip()
        )
        or DEFAULT_ORIGINS
    )
    #: Cap on rows any list endpoint will return, so a single query cannot pull
    #: the whole ais_position table (100k+ rows) into a response.
    max_page: int = 1000

    #: How many blocking handlers may run at once. This is a MEMORY bound, not
    #: a throughput knob, and it is the only one that holds when more than one
    #: person opens the map — a limit in the browser is per-page, so two viewers
    #: double it and ten viewers overwhelm any client-side cap.
    #:
    #: Measured on the scenario corpus, the map's opening burst costs about
    #: 65 MB per request in flight over a ~250 MB floor:
    #:
    #:     1 -> 317 MB    2 -> 389 MB    3 -> 447 MB    4 -> 516 MB    6 -> 650 MB
    #:
    #: The free deploy host allows 512 MB, and the published corpus is about a
    #: quarter larger than the one those numbers came from, which lands
    #: concurrency 3 just over the line and leaves 2 with real headroom. Hence
    #: the default. Raise it on a host with more memory — it costs nothing but
    #: memory, and on 0.1 of a CPU the requests were queueing behind each other
    #: regardless.
    #:
    #: Only handlers declared `def` are governed by this; `async def` ones run
    #: on the event loop and are deliberately outside it, which is what keeps
    #: /health answering while the queue is full.
    max_concurrent_queries: int = field(
        default_factory=lambda: max(
            1, int(os.getenv("MISR_MAX_CONCURRENT_QUERIES", "2") or 2)))

    #: What DuckDB is allowed to hold, and how many threads it may spawn.
    #:
    #: **DuckDB sizes both from the MACHINE, not from the container's share of
    #: it.** Left alone it reads total system RAM and claims 80% as its buffer
    #: pool — on a 16 GB host that is 10.6 GiB — and takes one thread per host
    #: core. Inside a 512 MB container on a large shared host that is a promise
    #: the kernel will not honour: the buffer pool grows toward a limit that
    #: does not exist, and the container is OOM-killed with the query still
    #: looking reasonable from the inside.
    #:
    #: This is why bounding query CONCURRENCY alone did not stop the restarts.
    #: A cap on how many queries run at once does nothing about how much one
    #: query believes it may keep.
    #:
    #: Sized to leave room for the ~165 MB the interpreter and pyarrow occupy
    #: before a byte is queried. Over the limit DuckDB spills to
    #: `temp_directory` rather than failing, so the cost of it being too low is
    #: latency, not an error.
    #: 128 MB, measured: the map's whole opening burst and every other tab
    #: complete without a single failure at 64 MB, so this is roughly double
    #: what the queries actually need and still leaves the 512 MB host a wide
    #: margin over the ~165 MB interpreter floor. The default must be right on
    #: its own — a blueprint's env vars are only applied when the blueprint
    #: re-syncs, not on a code redeploy, so a value that has to be typed into a
    #: dashboard to be safe is a value that will be missing when it matters.
    duckdb_memory_limit: str = field(
        default_factory=lambda: os.getenv("MISR_DUCKDB_MEMORY_LIMIT", "128MB"))
    duckdb_threads: int = field(
        default_factory=lambda: max(
            1, int(os.getenv("MISR_DUCKDB_THREADS", "2") or 2)))

    def header_name(self) -> str:
        return "X-API-Token"


settings = ApiSettings()
