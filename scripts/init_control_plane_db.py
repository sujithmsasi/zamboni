"""
Zamboni -- Control Plane DB init/migrate

Idempotent: creates the control-plane SQLite tables if missing, and applies
any pending column migrations if not. Safe to run on every deploy (invoked
from deploy/scripts/after_install.sh) -- CREATE TABLE IF NOT EXISTS and a
guarded ALTER TABLE loop, both no-ops once already applied.

Deliberately targets ONLY config.settings.ZAMBONI_CONTROL_PLANE_DB, never
ZAMBONI_LOCAL_DB (scripts/seed_local_db.py owns that file and seeds demo
data into it; this script never touches it and never seeds any data --
the control-plane DB starts genuinely empty, populated only by real UI/API
writes).

Usage:
    python scripts/init_control_plane_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.control_plane_schema import (  # noqa: E402
    CONTROL_PLANE_MIGRATIONS,
    CONTROL_PLANE_TABLES,
)
from config.settings import ZAMBONI_CONTROL_PLANE_DB  # noqa: E402
from engine.utils.local_db import create_tables, get_connection  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    log.info("init_control_plane_db.starting", path=ZAMBONI_CONTROL_PLANE_DB)
    create_tables(CONTROL_PLANE_TABLES, db_path=ZAMBONI_CONTROL_PLANE_DB)

    conn = get_connection(ZAMBONI_CONTROL_PLANE_DB)
    migrated = 0
    for table, col, sqlite_type in CONTROL_PLANE_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sqlite_type}")
            conn.commit()
            migrated += 1
        except Exception:
            pass  # column already exists

    print(f"Control-plane DB ready: {ZAMBONI_CONTROL_PLANE_DB}")
    print(f"  {len(CONTROL_PLANE_TABLES)} table(s), {migrated} new migration(s) applied")


if __name__ == "__main__":
    main()
