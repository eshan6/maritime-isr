"""Maritime ISR Phase 6 API — local FastAPI serving layer.

See `app.py` for the routes and `models.py` for the response contracts. Import
`create_app` to build a fresh application (the exercise tests do this against a
generated corpus)::

    from maritime_isr.api.app import create_app
"""
from __future__ import annotations

from .app import app, create_app

__all__ = ["app", "create_app"]
