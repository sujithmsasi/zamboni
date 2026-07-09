"""
Unit tests for scripts/control_plane_backup.py -- VACUUM INTO -> S3
snapshots and the two-tier (hourly/daily) retention prune.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import config.settings as settings
import scripts.control_plane_backup as backup_mod


def _key(dt: datetime) -> str:
    return f"control-plane-backups/zamboni_control_{dt.strftime('%Y%m%dT%H%M%SZ')}.db"


# ── take_backup ──────────────────────────────────────────────────────────────

def test_take_backup_vacuums_a_real_sqlite_file_and_uploads(tmp_path, monkeypatch):
    src = tmp_path / "source.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (name) VALUES ('hello')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(backup_mod, "ZAMBONI_CONTROL_PLANE_DB", str(src))
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/meta/")

    uploads = []

    def _fake_upload(bucket, key, local_path):
        # Verify the temp file is a real, valid, non-empty SQLite backup
        # -- taken via VACUUM INTO, not a placeholder or empty file.
        c = sqlite3.connect(local_path)
        rows = c.execute("SELECT name FROM t").fetchall()
        c.close()
        uploads.append((bucket, key, rows))

    monkeypatch.setattr(backup_mod, "upload_file", _fake_upload)

    key = backup_mod.take_backup()

    assert len(uploads) == 1
    bucket, uploaded_key, rows = uploads[0]
    assert bucket == "test-bucket"
    assert uploaded_key == key
    assert key.startswith("meta/control-plane-backups/zamboni_control_")
    assert key.endswith(".db")
    assert rows == [("hello",)]


# ── prune_backups ────────────────────────────────────────────────────────────

def test_prune_backups_keeps_newest_per_hour_and_per_day(monkeypatch):
    now = datetime.now(UTC)

    # Within the 24h hourly window: two backups in the same hour -- only
    # the newest should survive.
    hour_old_key = _key(now - timedelta(hours=2, minutes=40))
    hour_new_key = _key(now - timedelta(hours=2, minutes=10))
    # Within the 30d daily window (past 24h): two backups on the same day
    # -- only the newest should survive.
    day_old_key = _key(now - timedelta(days=5, hours=1))
    day_new_key = _key(now - timedelta(days=5, hours=0, minutes=5))
    # Older than the 30d window entirely -- always deleted.
    ancient_key = _key(now - timedelta(days=45))

    all_keys = [hour_old_key, hour_new_key, day_old_key, day_new_key, ancient_key]

    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: list(all_keys))

    deleted_calls = []
    monkeypatch.setattr(backup_mod, "delete_keys", lambda bucket, keys: deleted_calls.extend(keys) or len(keys))

    deleted_count = backup_mod.prune_backups(hourly_retention_hours=24, daily_retention_days=30)

    assert hour_old_key in deleted_calls
    assert hour_new_key not in deleted_calls
    assert day_old_key in deleted_calls
    assert day_new_key not in deleted_calls
    assert ancient_key in deleted_calls
    assert deleted_count == 3


def test_prune_backups_ignores_keys_not_matching_backup_pattern(monkeypatch):
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: ["control-plane-backups/README.txt"])

    deleted_calls = []
    monkeypatch.setattr(backup_mod, "delete_keys", lambda bucket, keys: deleted_calls.extend(keys) or len(keys))

    deleted_count = backup_mod.prune_backups()

    assert deleted_calls == []
    assert deleted_count == 0


def test_prune_backups_noop_when_nothing_to_prune(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: [_key(now)])

    called = []
    monkeypatch.setattr(backup_mod, "delete_keys", lambda bucket, keys: called.append(keys) or len(keys))

    deleted_count = backup_mod.prune_backups()

    assert deleted_count == 0
    # delete_keys is always called (its own empty-list early-return handles
    # the no-op case) but with nothing to delete -- a single fresh backup
    # is the newest (and only) entry in its hour bucket.
    assert called == [[]]


# ── _run_forever local-mode guard ────────────────────────────────────────────

def test_run_forever_returns_immediately_in_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", True)
    backup_mod._run_forever()
