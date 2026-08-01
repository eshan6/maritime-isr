"""Shared test fixtures.

The one thing here is a **pristine snapshot of the landed corpus**, taken once
before any test runs. Several test modules open the default-path graph
(`data/graph.sqlite`) and mutate it — dispose alerts, repopulate, clear — so by
the time the API exercise module runs, the ambient `data/` is no longer the
clean scenario corpus it was at session start. The API tests need a known-good
corpus, so they restore from this snapshot rather than trusting live `data/`.

Session-scoped and autouse so the copy happens before the first test body, which
is the only moment `data/` is guaranteed pristine. If no corpus is landed (a bare
checkout / CI) the snapshot is None and the API tests skip.
"""
from __future__ import annotations

import shutil

import pytest

from maritime_isr.config import cfg

#: Filled in by the session fixture; read by `pristine_corpus`.
_SNAPSHOT: dict = {"path": None}

_SNAPSHOT_MEMBERS = ("conformed", "misr.duckdb", "graph.sqlite")


def _corpus_landed() -> bool:
    root = cfg.data_root / "conformed" / "gfw_vessel_identity"
    return any(root.glob("day=*/part.parquet"))


@pytest.fixture(scope="session", autouse=True)
def _snapshot_pristine_corpus(tmp_path_factory):
    """Copy the landed corpus aside once, before any test can mutate it."""
    if not _corpus_landed():
        _SNAPSHOT["path"] = None
        yield None
        return
    snap = tmp_path_factory.mktemp("pristine_corpus")
    src = cfg.data_root
    for name in _SNAPSHOT_MEMBERS:
        s = src / name
        if s.is_dir():
            shutil.copytree(s, snap / name)
        elif s.exists():
            shutil.copy2(s, snap / name)
    _SNAPSHOT["path"] = snap
    yield snap


@pytest.fixture(scope="session")
def pristine_corpus(_snapshot_pristine_corpus):
    """The pristine snapshot directory, or None if no corpus is landed."""
    return _SNAPSHOT["path"]
