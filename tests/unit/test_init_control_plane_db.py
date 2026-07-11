"""
Unit tests for scripts/init_control_plane_db.py's restore-on-empty auto-heal
(2026-07-10 audit fix).

Real gap closed: control_plane_backup.py has been taking S3 backups since
it shipped, but nothing ever restored one -- combined with
deploy/zamboni-cfn.yaml's DeleteOnTermination=true root EBS volume, a real
EC2 instance replacement (confirmed to have already happened once, per
the 2026-07-10 UserData-hardening CLAUDE.md entry) would silently start
the control-plane DB empty with no way back. _is_genuinely_empty() +
_attempt_restore_on_empty() close that gap: every table verified empty
right after create_tables() triggers an automatic restore from the newest
S3 backup, but a normal redeploy on an existing (non-empty) instance never
reaches this branch at all -- it can only fill in a genuinely blank slate,
never clobber live data.
"""
from __future__ import annotations

import pytest

import config.settings as settings
import engine.utils.local_db as local_db
import scripts.control_plane_backup as backup_mod
import scripts.init_control_plane_db as init_mod


def _fresh_db(tmp_path, monkeypatch) -> str:
    db_path = str(tmp_path / "control.db")
    monkeypatch.setattr(local_db, "_conns", {})
    init_mod.create_tables(init_mod.CONTROL_PLANE_TABLES, db_path=db_path)
    return db_path


# ── _is_genuinely_empty ──────────────────────────────────────────────────────

def test_is_genuinely_empty_true_right_after_create_tables(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    assert init_mod._is_genuinely_empty(db_path) is True


def test_is_genuinely_empty_false_when_any_table_has_a_row(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    conn = local_db.get_connection(db_path)
    conn.execute(
        "INSERT INTO domain_registry (domain_name, environment, owner_email) VALUES ('finance', 'prod', 'a@b.com')"
    )
    conn.commit()

    assert init_mod._is_genuinely_empty(db_path) is False


# ── _attempt_restore_on_empty ─────────────────────────────────────────────────

def test_attempt_restore_calls_restore_latest_with_db_path_and_drops_cached_connection(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    assert db_path in local_db._conns  # sanity: create_tables cached a connection

    calls = []
    monkeypatch.setattr(
        backup_mod, "restore_latest",
        lambda dest: calls.append(dest) or "control-plane-backups/zamboni_control_20260710T000000Z.db",
    )

    init_mod._attempt_restore_on_empty(db_path)

    assert calls == [db_path]
    # The cached connection must be dropped/closed before overwriting the
    # file on disk -- a live sqlite3.Connection doesn't notice a swap.
    assert db_path not in local_db._conns


def test_attempt_restore_no_backup_available_leaves_empty_db(tmp_path, monkeypatch, capsys):
    db_path = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_mod, "restore_latest", lambda dest: None)

    init_mod._attempt_restore_on_empty(db_path)  # must not raise

    out = capsys.readouterr().out
    assert "no S3 backup was found" in out.lower() or "starting empty" in out.lower()


def test_attempt_restore_failure_recreates_empty_tables(tmp_path, monkeypatch):
    """If restore_latest() itself raises (a real S3 error), the script
    must still leave a working -- if empty -- control-plane DB behind
    rather than a half-written file."""
    db_path = _fresh_db(tmp_path, monkeypatch)

    def _raise(dest):
        raise RuntimeError("S3 unavailable")

    monkeypatch.setattr(backup_mod, "restore_latest", _raise)

    init_mod._attempt_restore_on_empty(db_path)  # must not raise

    conn = local_db.get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) FROM domain_registry").fetchone()[0]
    assert count == 0


# ── main() wiring ─────────────────────────────────────────────────────────────

def test_main_skips_restore_check_in_local_mode(tmp_path, monkeypatch):
    """ZAMBONI_LOCAL_MODE is never the deployment path this auto-heal
    exists for -- must not attempt a real S3 call in that mode."""
    db_path = str(tmp_path / "control.db")
    monkeypatch.setattr(local_db, "_conns", {})
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", True)
    monkeypatch.setattr(init_mod, "ZAMBONI_LOCAL_MODE", True)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_DB", db_path)

    calls = []
    monkeypatch.setattr(init_mod, "_attempt_restore_on_empty", lambda path: calls.append(path))

    init_mod.main()

    assert calls == []


def test_main_attempts_restore_when_empty_outside_local_mode(tmp_path, monkeypatch):
    db_path = str(tmp_path / "control.db")
    monkeypatch.setattr(local_db, "_conns", {})
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_DB", db_path)
    # The restore mock below is a no-op, so the DB is still empty
    # afterward -- acknowledge that as a first install so main() can
    # proceed far enough to prove _attempt_restore_on_empty was actually
    # called, without also exercising the separate fail-closed behavior
    # (covered by its own tests below).
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_FIRST_INSTALL", True)

    calls = []
    monkeypatch.setattr(init_mod, "_attempt_restore_on_empty", lambda path: calls.append(path))

    init_mod.main()

    assert calls == [db_path]


# ── fail-closed startup (2026-07-11 audit fix) ────────────────────────────────

def test_main_raises_when_still_empty_after_restore_and_not_first_install(tmp_path, monkeypatch):
    """The core fail-closed guarantee: a replacement instance with no
    usable S3 backup must NOT silently start with an empty production
    control plane."""
    db_path = str(tmp_path / "control.db")
    monkeypatch.setattr(local_db, "_conns", {})
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_DB", db_path)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_FIRST_INSTALL", False)
    monkeypatch.setattr(init_mod, "_attempt_restore_on_empty", lambda path: None)  # no-op, stays empty

    with pytest.raises(init_mod.ControlPlaneEmptyAndNoBackupError):
        init_mod.main()


def test_main_proceeds_when_first_install_flag_acknowledges_empty_start(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "control.db")
    monkeypatch.setattr(local_db, "_conns", {})
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_DB", db_path)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_FIRST_INSTALL", True)
    monkeypatch.setattr(init_mod, "_attempt_restore_on_empty", lambda path: None)

    init_mod.main()  # must not raise

    out = capsys.readouterr().out
    assert "first_install" in out.lower() or "acknowledged" in out.lower()


# ── _apply_migrations / _verify_schema (2026-07-11 audit fix) ────────────────

def test_apply_migrations_adds_all_pending_columns(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    conn = local_db.get_connection(db_path)

    init_mod._apply_migrations(conn)

    # Every migration column must exist afterward, regardless of whether
    # it was already present in the base table DDL (some
    # CONTROL_PLANE_MIGRATIONS entries are kept for historical/idempotency
    # reasons even though the base DDL has since absorbed them) or newly
    # added by this call.
    for table, col, _sqlite_type in init_mod.CONTROL_PLANE_MIGRATIONS:
        assert col in init_mod._existing_columns(conn, table)


def test_apply_migrations_idempotent_second_call_adds_nothing(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    conn = local_db.get_connection(db_path)

    init_mod._apply_migrations(conn)
    second_run_migrated = init_mod._apply_migrations(conn)

    assert second_run_migrated == 0


def test_apply_migrations_rolls_back_all_on_mid_failure(tmp_path, monkeypatch):
    """A failure partway through must roll back EVERY migration in this
    run, not leave the earlier ones applied and only the failing one
    skipped -- a half-migrated schema is exactly what transactional DDL
    exists to prevent here."""
    db_path = _fresh_db(tmp_path, monkeypatch)
    conn = local_db.get_connection(db_path)

    # Some CONTROL_PLANE_MIGRATIONS entries are already present in the
    # base table DDL (kept for historical/idempotency reasons) -- find
    # genuinely pending ones to use as the "should be rolled back" probes,
    # rather than assuming any particular index is actually pending.
    pending = [
        (t, c, s) for t, c, s in init_mod.CONTROL_PLANE_MIGRATIONS
        if c not in init_mod._existing_columns(conn, t)
    ]
    assert len(pending) >= 2, "fixture assumption: need >=2 genuinely pending migrations to test rollback"

    bad_migrations = [pending[0], ("no_such_table", "some_col", "TEXT"), pending[1]]
    monkeypatch.setattr(init_mod, "CONTROL_PLANE_MIGRATIONS", bad_migrations)

    with pytest.raises(Exception):  # noqa: B017 -- sqlite3.OperationalError, not our own type
        init_mod._apply_migrations(conn)

    # pending[0] must NOT have taken effect either, even though it was
    # processed successfully before the failing entry -- the whole batch
    # rolls back together.
    table0, col0, _ = pending[0]
    assert col0 not in init_mod._existing_columns(conn, table0)


def test_verify_schema_passes_after_full_migration(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    conn = local_db.get_connection(db_path)
    init_mod._apply_migrations(conn)

    init_mod._verify_schema(conn)  # must not raise


def test_verify_schema_raises_on_missing_table(tmp_path, monkeypatch):
    db_path = str(tmp_path / "incomplete.db")
    monkeypatch.setattr(local_db, "_conns", {})
    conn = local_db.get_connection(db_path)
    conn.execute("CREATE TABLE domain_registry (domain_name TEXT PRIMARY KEY)")
    conn.commit()

    with pytest.raises(init_mod.ControlPlaneSchemaVerificationError, match="missing expected table"):
        init_mod._verify_schema(conn)


def test_verify_schema_raises_on_missing_column(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    conn = local_db.get_connection(db_path)
    # Deliberately skip _apply_migrations -- base tables exist but none of
    # CONTROL_PLANE_MIGRATIONS' columns have been added yet.

    with pytest.raises(init_mod.ControlPlaneSchemaVerificationError, match="missing expected column"):
        init_mod._verify_schema(conn)


def test_main_does_not_raise_when_restore_actually_filled_the_db(tmp_path, monkeypatch):
    """If _attempt_restore_on_empty() genuinely restored data, the
    fail-closed check must not fire at all -- it only triggers when the
    DB is STILL empty afterward."""
    db_path = str(tmp_path / "control.db")
    monkeypatch.setattr(local_db, "_conns", {})
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_DB", db_path)
    monkeypatch.setattr(init_mod, "ZAMBONI_CONTROL_PLANE_FIRST_INSTALL", False)

    def _fake_restore(path):
        conn = local_db.get_connection(path)
        conn.execute(
            "INSERT INTO domain_registry (domain_name, environment, owner_email) VALUES ('finance', 'prod', 'a@b.com')"
        )
        conn.commit()

    monkeypatch.setattr(init_mod, "_attempt_restore_on_empty", _fake_restore)

    init_mod.main()  # must not raise
