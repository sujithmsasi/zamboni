"""
Zamboni -- Control Plane Integrity Check

One-shot script, run daily by deploy/systemd/zamboni-control-plane-
integrity.timer. Runs SQLite's PRAGMA integrity_check against the
control-plane DB -- deeper than the SELECT 1 liveness check in app_start.sh,
which only confirms the file opens and responds, not that its pages are
structurally sound. Corruption here should page loudly, not log-and-continue
-- silently running against a corrupt control-plane DB risks writing bad
data or losing rows without anyone noticing until much later.

Usage:
    python scripts/control_plane_integrity_check.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import ZAMBONI_CONTROL_PLANE_DB, ZAMBONI_LOCAL_MODE  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> int:
    if ZAMBONI_LOCAL_MODE:
        log.info("control_plane_integrity_check.skipped_local_mode")
        print("Skipped -- ZAMBONI_LOCAL_MODE is true, no control-plane DB in this mode.")
        return 0

    conn = sqlite3.connect(ZAMBONI_CONTROL_PLANE_DB)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()

    ok = len(rows) == 1 and rows[0][0] == "ok"
    if ok:
        log.info("control_plane_integrity_check.ok", path=ZAMBONI_CONTROL_PLANE_DB)
        print(f"Control-plane DB integrity OK: {ZAMBONI_CONTROL_PLANE_DB}")
        return 0

    problems = [r[0] for r in rows]
    log.error("control_plane_integrity_check.failed", path=ZAMBONI_CONTROL_PLANE_DB, problems=problems)
    print(f"Control-plane DB integrity check FAILED: {problems}")

    try:
        from engine.core.notifier import send_alert
        send_alert(
            subject="Control plane DB integrity check FAILED",
            message=(
                f"PRAGMA integrity_check reported problems against "
                f"{ZAMBONI_CONTROL_PLANE_DB}:\n\n" + "\n".join(f"  - {p}" for p in problems) +
                "\n\nRestore from the latest S3 backup "
                "(s3://.../control-plane-backups/) and investigate before "
                "trusting further writes to this file."
            ),
        )
    except Exception as e:
        log.error("control_plane_integrity_check.alert_failed", error=str(e))

    return 1


if __name__ == "__main__":
    sys.exit(main())
