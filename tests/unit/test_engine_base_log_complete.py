"""
Regression test for engine/engines/base.py::BaseEngine._log_complete().

Real, severe bug found 2026-07-09 while testing an unrelated
lifecycle_engine.py change: summary_dict() always returns a dict
containing "engine", "run_id", and "elapsed_seconds" keys, and
_log_complete() ALSO passes those same three as explicit keyword
arguments while spreading **result -- Python raises TypeError ("got
multiple values for keyword argument") for any call shaped like
f(engine=x, **{"engine": y}), unconditionally, independent of structlog's
own internals. This meant every top-level HKEngine.run(),
ArchivalEngine.run(), and LifecycleEngine.run()/run_scan()/run_cleanup()
call crashed with an unhandled TypeError on this line -- right after all
real maintenance work for the run had already completed -- so the actual
engine.scripts.run_hk/run_archival/run_cleanup/run_lifecycle_cycle/
run_lifecycle_scan.py entry points (the real EventBridge/Control-M-
triggered jobs) never returned a result or exited cleanly. Reproduced
directly against real (unmocked) engine instances before fixing; before
this fix, ZERO tests in this suite called any top-level .run()/.run_scan()/
.run_cleanup() end-to-end without mocking around this exact line, which is
how it went uncaught.
"""
from __future__ import annotations

import pytest

from engine.engines.archival_engine import ArchivalEngine
from engine.engines.hk_engine import HKEngine
from engine.engines.lifecycle_engine import LifecycleEngine


@pytest.mark.parametrize("cls", [HKEngine, ArchivalEngine, LifecycleEngine])
def test_log_complete_does_not_crash_on_summary_dict_output(cls):
    """summary_dict()'s own output must always be safe to pass straight
    into _log_complete() -- this is the exact call shape every engine's
    run() method uses at the end of a run."""
    engine = cls(dry_run=True)
    result = engine.summary_dict(tables_processed=3, succeeded=2, failed=1, skipped=0)
    engine._log_complete(result)  # must not raise


def test_log_complete_preserves_result_fields_in_the_log_event(monkeypatch):
    """The fix must not silently drop real summary data (succeeded/failed/
    skipped/etc.) while working around the duplicate-kwarg collision."""
    import engine.engines.base as base_mod

    captured = {}
    monkeypatch.setattr(
        base_mod.log, "info",
        lambda event, **kw: captured.update(kw) or captured.__setitem__("_event", event),
    )

    engine = HKEngine(dry_run=True)
    result = engine.summary_dict(tables_processed=5, succeeded=4, failed=1, skipped=0, discovered=5)
    engine._log_complete(result)

    assert captured["_event"] == "engine.run_complete"
    assert captured["engine"] == "HKEngine"
    assert captured["tables_processed"] == 5
    assert captured["succeeded"] == 4
    assert captured["failed"] == 1
    assert captured["discovered"] == 5
