"""`python -m maritime_isr.api` — run the local API with uvicorn.

Binds to 127.0.0.1 only. This is a laptop demo surface (ADR-013), never exposed
beyond localhost; the token in `settings` is a convenience, not a security
boundary.
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("MISR_API_HOST", "127.0.0.1")
    port = int(os.getenv("MISR_API_PORT", "8000"))
    uvicorn.run("maritime_isr.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
