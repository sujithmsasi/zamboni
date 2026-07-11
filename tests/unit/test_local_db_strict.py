"""
Unit tests for engine/utils/local_db.py's strict read/write variants
(read_sql_strict/run_query_strict), added 2026-07-11 as the production
control-plane counterpart to the lenient read_sql_local()/
run_query_local() -- those stay unchanged (still used by local/demo UI
simulation), which is exactly why these are NEW functions rather than a
behavior change to the existing ones.
"""
from __future__ import annotations

import pytest

import engine.utils.local_db as local_db
from engine.utils.local_db import (
    LocalDbReadError,
    LocalDbWriteError,
    read_sql_strict,
    run_query_strict,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "strict_test.db")
    monkeypatch.setattr(local_db, "_conns", {})
    conn = local_db.get_connection(db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'b')")
    conn.commit()
    yield db_path
    monkeypatch.setattr(local_db, "_conns", {})


# ── read_sql_strict ───────────────────────────────────────────────────────────

def test_read_sql_strict_returns_real_rows(db):
    df = read_sql_strict("SELECT * FROM t ORDER BY id", db_path=db)
    assert len(df) == 2
    assert list(df["name"]) == ["a", "b"]


def test_read_sql_strict_empty_result_is_not_an_error(db):
    """A query that runs fine and matches zero rows must return an empty
    DataFrame, not raise -- only a genuine failure raises."""
    df = read_sql_strict("SELECT * FROM t WHERE id = 999", db_path=db)
    assert df.empty


def test_read_sql_strict_raises_on_missing_table(db):
    with pytest.raises(LocalDbReadError):
        read_sql_strict("SELECT * FROM no_such_table", db_path=db)


def test_read_sql_strict_raises_on_malformed_sql(db):
    with pytest.raises(LocalDbReadError):
        read_sql_strict("SELECT FROM WHERE this is not sql", db_path=db)


# ── run_query_strict ──────────────────────────────────────────────────────────

def test_run_query_strict_real_write_applies(db):
    result = run_query_strict("UPDATE t SET name = 'z' WHERE id = 1", db_path=db)
    assert result is not None
    conn = local_db.get_connection(db)
    row = conn.execute("SELECT name FROM t WHERE id = 1").fetchone()
    assert row[0] == "z"


def test_run_query_strict_raises_on_missing_table(db):
    with pytest.raises(LocalDbWriteError):
        run_query_strict("UPDATE no_such_table SET x = 1", db_path=db)


def test_run_query_strict_expect_rowcount_false_allows_zero_rows(db):
    """Default behavior (expect_rowcount=False) must not raise on a
    legitimate zero-row match -- e.g. a bulk update whose filter matches
    nothing, or an idempotent delete-if-exists."""
    result = run_query_strict("UPDATE t SET name = 'z' WHERE id = 999", db_path=db)
    assert result is not None


def test_run_query_strict_expect_rowcount_true_raises_on_zero_rows(db):
    """A single-row-keyed UPDATE (expect_rowcount=True) matching zero
    rows must raise -- the caller's key doesn't exist or was changed
    concurrently, and silently 'succeeding' would hide that."""
    with pytest.raises(LocalDbWriteError, match="0 rows"):
        run_query_strict("UPDATE t SET name = 'z' WHERE id = 999", db_path=db, expect_rowcount=True)


def test_run_query_strict_expect_rowcount_true_passes_when_rows_matched(db):
    result = run_query_strict("UPDATE t SET name = 'z' WHERE id = 1", db_path=db, expect_rowcount=True)
    assert result is not None
