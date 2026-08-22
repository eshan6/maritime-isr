"""Maritime ISR Phase 6 API — local FastAPI serving layer.

See `app.py` for the routes and `models.py` for the response contracts. Import
`create_app` to build a fresh application (the exercise tests do this against a
generated corpus)::

    from maritime_isr.api.app import create_app

**Nothing is imported eagerly here, and that is load-bearing rather than
tidiness.** This module used to do ``from .app import app, create_app`` at
import time, which meant that importing *anything* under ``maritime_isr.api``
— including ``api.reader``, the DuckDB read layer that has no web dependency at
all — constructed the whole FastAPI application as a side effect.

That turned into a genuine import cycle the moment a non-API module needed the
reader: ``assistant`` imports ``api.reader``, which ran this file, which imported
``api.app``, which imports ``assistant`` — arriving back at a half-initialised
module and failing on the first attribute it touched. The read layer is a
*storage* concern that the API happens to use, not the other way round, and an
eager re-export here inverted that.

PEP 562 module ``__getattr__`` keeps the public surface identical —
``from maritime_isr.api import app`` and ``uvicorn maritime_isr.api:app`` both
still resolve — while making the cost and the dependency arrive only when
somebody actually asks for the application.
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    """Resolve ``app`` / ``create_app`` from the submodule on first access.

    ``importlib.import_module`` rather than ``from . import app``: the submodule
    ``maritime_isr.api.app`` and the FastAPI instance ``app`` share a name, so
    the ``from`` form re-enters this very function looking for the attribute it
    is trying to define and recurses until the stack runs out. Naming the module
    absolutely sidesteps the collision entirely.
    """
    if name in __all__:
        return getattr(importlib.import_module(f"{__name__}.app"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
