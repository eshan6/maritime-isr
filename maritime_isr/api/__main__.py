"""`python -m maritime_isr.api` — run the local API with uvicorn.

Binds to 127.0.0.1 by DEFAULT — a laptop demo surface (ADR-013). The host and
port are env-overridable because the Hugging Face Space has to bind 0.0.0.0:7860
to be reachable at all; see `deploy/huggingface/Dockerfile`.

So this is no longer localhost-only in every deployment, and the token in
`settings` was never a security boundary in any of them. The public Space is
acceptable because its corpus is synthetic, not because the token guards it.
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
