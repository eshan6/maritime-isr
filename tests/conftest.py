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


def _build_world(seed: int = 7):
    """Build a scenario world the way `run.generate` builds it.

    **The one way to construct a world in tests**, so that what the tests assert
    on is the corpus production actually produces. It lives here rather than in
    one test module because four modules were each building their own, and they
    had already drifted.

    The step that matters is `reserve_against_corpus`. `run.generate` calls it
    before a single hull is named, so the cast is minted *around* whichever
    reserved-band identifiers a real transmitter on this machine has already
    broadcast. The test modules skipped it and went straight to
    `ScenarioWorld.new` — harmless only for as long as the cast stayed small
    enough never to reach a reserved number.

    That ran out when the corpus grew from 253 hulls to 674: the extra serials
    walked into reserved IMO 1005253 and the collision guard failed the build.
    The corpus was never wrong. The fixture had been building a world production
    does not build, so a real production guarantee could only ever surface here
    as a spurious failure — and a spurious failure in a guard against identifier
    collisions is precisely the one you eventually learn to ignore.
    """
    from maritime_isr.scenario import ScenarioWorld
    from maritime_isr.scenario.cast import build_cast
    from maritime_isr.scenario.identifiers import reserve_against_corpus
    from maritime_isr.scenario.profile import CorpusProfile
    from maritime_isr.scenario.scenarios import run_all

    profile = CorpusProfile.load()
    reserve_against_corpus(profile=profile)
    w = ScenarioWorld.new(seed, profile)
    build_cast(w)
    run_all(w)
    w.identity.close_window(w.t1)
    return w


@pytest.fixture(scope="session")
def build_world():
    """The world builder, handed to tests as a fixture.

    A fixture rather than a plain import because `tests/` is not on `sys.path`
    — `from conftest import ...` is a `ModuleNotFoundError` here — and pytest
    delivers conftest fixtures to every module under it without one. It yields
    the *function*, not a world, because `test_generation_is_robust_across_seeds`
    has to build four seeds and a fixture that returned a single world could not
    serve it.
    """
    return _build_world
