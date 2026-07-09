"""
Zamboni -- Control Plane Backup Loop

Long-running process (own systemd unit, deploy/systemd/
zamboni-control-plane-backup.service) that periodically snapshots the
SQLite control-plane DB to S3, now that it's primary storage for
stream_registry/hk_config/domain_registry/nonprod_registry/controlm_jobs,
not a disposable cache. Uses SQLite's own VACUUM INTO to produce a
transactionally-consistent snapshot even under concurrent WAL writers --
never a raw file copy of a live database file, which could capture a
torn/inconsistent state mid-write.

Retention, two tiers (see prune_backups()):
  - hourly: keep the newest backup per hour, for the most recent
    control_plane_backup_hourly_retention_hours (default 24)
  - daily:  keep the newest backup per day, for the most recent
    control_plane_backup_daily_retention_days (default 30)
  - anything older than the daily window is deleted. No EBS snapshot tier
    -- deliberately ruled out, S3 backups are the only durability layer.

Usage:
    python scripts/control_plane_backup.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.platform_settings import get_settings  # noqa: E402
from config.settings import ZAMBONI_CONTROL_PLANE_DB, ZAMBONI_METADATA_BUCKET  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402
from engine.utils.s3_client import delete_keys, list_keys, parse_s3_uri, upload_file  # noqa: E402

log = get_logger(__name__)

_MIN_TICK_SECONDS = 15
_PREFIX = "control-plane-backups/"
_KEY_TS_RE = re.compile(r"zamboni_control_(\d{8}T\d{6}Z)\.db$")


def take_backup() -> str:
    """
    VACUUM INTO a temp file, upload to S3, return the key. Never a raw
    file copy of the live DB -- VACUUM INTO produces a consistent
    snapshot even mid-write under WAL, unlike copying the .db file
    directly (which could capture a torn page).
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key_name = f"zamboni_control_{ts}.db"
    bucket, prefix = parse_s3_uri(ZAMBONI_METADATA_BUCKET)
    full_key = f"{prefix}{_PREFIX}{key_name}" if prefix else f"{_PREFIX}{key_name}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = str(Path(tmp) / "zamboni_control_backup.db")
        conn = sqlite3.connect(ZAMBONI_CONTROL_PLANE_DB)
        try:
            conn.execute("VACUUM INTO ?", (tmp_path,))
        finally:
            conn.close()
        upload_file(bucket, full_key, tmp_path)

    log.info("control_plane_backup.taken", key=full_key)
    return full_key


def _parse_backup_ts(key: str) -> datetime | None:
    m = _KEY_TS_RE.search(key)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def prune_backups(hourly_retention_hours: int = 24, daily_retention_days: int = 30) -> int:
    """
    Keep the newest backup per hour for the most recent
    hourly_retention_hours, the newest backup per day for the most recent
    daily_retention_days, delete everything else (including anything
    older than the daily window). Returns count deleted.
    """
    bucket, prefix = parse_s3_uri(ZAMBONI_METADATA_BUCKET)
    list_prefix = f"{prefix}{_PREFIX}" if prefix else _PREFIX
    keys = list_keys(bucket, list_prefix)

    dated = [(ts, key) for key in keys if (ts := _parse_backup_ts(key)) is not None]
    if not dated:
        return 0

    now = datetime.now(UTC)
    hour_cutoff = now - timedelta(hours=hourly_retention_hours)
    day_cutoff  = now - timedelta(days=daily_retention_days)

    keep: dict[str, tuple[datetime, str]] = {}  # bucket key -> (ts, key), newest wins
    to_delete: list[str] = []

    for ts, key in dated:
        if ts >= hour_cutoff:
            bucket_key = f"h:{ts.strftime('%Y-%m-%d-%H')}"
        elif ts >= day_cutoff:
            bucket_key = f"d:{ts.strftime('%Y-%m-%d')}"
        else:
            to_delete.append(key)
            continue

        existing = keep.get(bucket_key)
        if existing is None or ts > existing[0]:
            if existing is not None:
                to_delete.append(existing[1])
            keep[bucket_key] = (ts, key)
        else:
            to_delete.append(key)

    deleted = delete_keys(bucket, to_delete)
    if deleted:
        log.info("control_plane_backup.pruned", deleted=deleted, kept=len(keep))
    return deleted


def _run_forever() -> None:
    from config.settings import ZAMBONI_LOCAL_MODE
    if ZAMBONI_LOCAL_MODE:
        # No real control-plane DB or S3 to back up in local mode -- matches
        # scripts/control_plane_sync.py's and aws_smoke_test.py's convention.
        log.info("control_plane_backup.skipped_local_mode")
        return

    last_backup_at = 0.0

    while True:
        settings = get_settings()
        interval = max(int(settings.get("control_plane_backup_interval_seconds", 300)), _MIN_TICK_SECONDS)
        hourly_hours = int(settings.get("control_plane_backup_hourly_retention_hours", 24))
        daily_days   = int(settings.get("control_plane_backup_daily_retention_days", 30))

        now = time.monotonic()
        if now - last_backup_at >= interval:
            try:
                take_backup()
                prune_backups(hourly_hours, daily_days)
            except Exception as e:
                log.error("control_plane_backup.cycle_failed", error=str(e))
            last_backup_at = now

        time.sleep(min(interval, _MIN_TICK_SECONDS * 2))


if __name__ == "__main__":
    log.info("control_plane_backup.starting")
    _run_forever()
