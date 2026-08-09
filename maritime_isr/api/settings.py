"""API settings — local-only serving, token auth, dev-frontend CORS.

This is a demo surface for a laptop, not a public service. The token is a
shared secret read from the environment, and CORS is opened only to the local
dev frontend origins. Both are deliberately simple; ADR-013 keeps the whole
system on one machine with no deploy host, so there is no multi-tenant auth to
build here yet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Default dev token. Overridden by MISR_API_TOKEN. A default exists so the demo
#: runs out of the box on the laptop; it is not a secret and must be changed if
#: this is ever exposed beyond localhost (it is not, today).
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

    def header_name(self) -> str:
        return "X-API-Token"


settings = ApiSettings()
