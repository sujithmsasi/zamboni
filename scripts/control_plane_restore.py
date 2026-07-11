"""
Zamboni — Control Plane Restore CLI (2026-07-10 audit fix)

Manual, human-operated recovery for the SQLite control-plane DB
(stream_registry, hk_config, domain_registry, nonprod_registry,
controlm_jobs) from an S3 backup taken by scripts/control_plane_backup.py.

Real gap this closes: control_plane_backup.py has been taking backups
since it shipped, but nothing in this codebase ever read one back. A real
instance replacement (mitigated but not fully eliminated by the dedicated
retained EBS volume in deploy/zamboni-cfn.yaml -- see
ControlPlaneVolumeSizeGiB there) can still destroy
/data/zamboni/zamboni_control.db; this is the tool an operator reaches
for when that happens and scripts/init_control_plane_db.py's own
automatic restore-on-empty (see that script) either didn't fire or
picked the wrong backup.

2026-07-11 audit fix: routes through
scripts/control_plane_backup._restore_key(), which downloads to a temp
file, runs PRAGMA integrity_check + a schema check, and only then
atomically replaces dest -- a corrupted or schema-incompatible backup is
rejected with dest left untouched, same guarantee the automatic
restore-on-empty path gets.

Usage:
    python scripts/control_plane_restore.py
    python scripts/control_plane_restore.py --latest
    python scripts/control_plane_restore.py --key control-plane-backups/zamboni_control_20260710T120000Z.db
    python scripts/control_plane_restore.py --dest /data/zamboni/zamboni_control.db --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import ZAMBONI_CONTROL_PLANE_DB  # noqa: E402


def _print_backups(backups: list[tuple]) -> None:
    print(f"\n{len(backups)} backup(s) available (newest first):\n")
    for i, (ts, key) in enumerate(backups, start=1):
        print(f"  [{i}] {ts.isoformat()}  {key}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Zamboni control-plane DB restore tool")
    parser.add_argument("--dest", default=ZAMBONI_CONTROL_PLANE_DB, help="Local path to restore into")
    parser.add_argument("--key", default=None, help="Specific S3 backup key (skips selection)")
    parser.add_argument("--latest", action="store_true", help="Restore the newest backup without prompting")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=False)
    args = parser.parse_args()

    from scripts.control_plane_backup import BackupValidationError, list_backups

    backups = list_backups()
    if not backups:
        print("No control-plane backups found in S3. Nothing to restore.")
        return 1

    if args.key:
        key = args.key
    elif args.latest:
        _, key = backups[0]
        print(f"Restoring newest backup: {key}")
    else:
        _print_backups(backups)
        try:
            choice = input(f"\nSelect a backup [1-{len(backups)}] or 'q' to quit: ").strip()
        except EOFError:
            choice = "q"
        if choice.lower() == "q":
            print("Aborted.")
            return 1
        try:
            _, key = backups[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return 1

    dest = Path(args.dest)
    print(f"\nRestore plan:\n  backup key : {key}\n  restore to : {dest}\n")

    if args.dry_run:
        print("DRY RUN — no changes made.")
        return 0

    confirm = input(f"Type RESTORE to overwrite {dest} with this backup: ").strip()
    if confirm != "RESTORE":
        print("Confirmation did not match. Aborted.")
        return 1

    if dest.exists():
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        preserved = dest.with_name(f"{dest.name}.pre-restore-{ts}")
        shutil.copy2(dest, preserved)
        print(f"Existing file preserved at: {preserved}")

    from scripts.control_plane_backup import _restore_key

    try:
        _restore_key(key, str(dest))
    except BackupValidationError as e:
        print(f"\nRefused: backup failed validation, {dest} was NOT modified.\n  {e}")
        return 1

    print(f"\nRestored {key} -> {dest}")
    print("Restart zamboni-api / zamboni-control-plane-sync so they pick up the restored file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
