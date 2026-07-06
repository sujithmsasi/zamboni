"""
Zamboni — Metadata Rollback CLI (Workstream A / Phase 1c)

Turns "metadata lost" into a guided, audited 5-minute fix. Lists rollback
candidates from engine.core.recovery.get_rollback_candidates(), validates
the chosen target (refusing if it was likely orphan-deleted -- see
ORPHAN_MIN_AGE_HOURS_FLOOR in engine/core/recovery.py), requires typing the
table name to confirm a real (non-dry-run) rollback, then calls
engine.core.recovery.rollback_metadata().

Usage:
    python scripts/recover_metadata.py --fqn glue_catalog.db.tbl
    python scripts/recover_metadata.py --fqn glue_catalog.db.tbl --to s3://bucket/.../00042-xyz.metadata.json
    python scripts/recover_metadata.py --fqn glue_catalog.db.tbl --dry-run
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os

from config.settings import AWS_REGION, DRY_RUN_DEFAULT, ZAMBONI_LOCAL_MODE, get_mode  # noqa: E402


def _apply_boto3_session() -> None:
    """contracts.md §2: route this process's default boto3 session through
    get_mode() so glue_client/s3_client's lazily constructed clients pick up
    the aws_local SSO profile in demo mode. No-op in local mode -- nothing
    here calls AWS, and boto3.Session().profile_name always resolves to the
    literal string "default" even with no profile configured, so blindly
    forwarding it into setup_default_session() would force a profile lookup
    that fails on a machine with no ~/.aws/config at all."""
    if ZAMBONI_LOCAL_MODE:
        return
    import boto3
    if get_mode() == "aws_local":
        boto3.setup_default_session(
            profile_name=os.getenv("AWS_SSO_PROFILE", "prod-toolsgenai-sso"),
            region_name=AWS_REGION,
        )
    else:
        boto3.setup_default_session(region_name=AWS_REGION)


def _print_candidates(candidates: list[dict]) -> None:
    print(f"\n{len(candidates)} rollback candidate(s) for this table:\n")
    for i, c in enumerate(candidates, start=1):
        print(
            f"  [{i}] {c.get('started_at')}  op={c.get('operation', ''):<10} "
            f"status={c.get('status', ''):<8} integrity={c.get('integrity_status')}"
        )
        print(f"       before: {c.get('metadata_location_before')}")
        print(f"       after:  {c.get('metadata_location_after')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Zamboni metadata rollback tool")
    parser.add_argument("--fqn", required=True, help="Table FQN, e.g. glue_catalog.db.tbl")
    parser.add_argument("--to", default=None, help="Target metadata.json S3 URI (skips candidate selection)")
    parser.add_argument("--reason", default=None, help="Business reason (prompted if omitted)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=DRY_RUN_DEFAULT)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    args = parser.parse_args()

    _apply_boto3_session()

    from engine.core.recovery import get_rollback_candidates, rollback_metadata, validate_rollback_target

    target = args.to
    if not target:
        candidates = get_rollback_candidates(args.fqn)
        if not candidates:
            print(f"No rollback candidates found for {args.fqn} "
                  "(no execution_log rows with metadata_location_before).")
            return 1
        _print_candidates(candidates)
        try:
            choice = input(f"\nSelect a candidate [1-{len(candidates)}] or 'q' to quit: ").strip()
        except EOFError:
            choice = "q"
        if choice.lower() == "q":
            print("Aborted.")
            return 1
        try:
            target = candidates[int(choice) - 1]["metadata_location_before"]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return 1

    print(f"\nValidating target:\n  {target}\n")
    validation = validate_rollback_target(args.fqn, target)
    print(f"  valid    : {validation.valid}")
    print(f"  reason   : {validation.reason}")
    if validation.snapshot_id is not None:
        print(f"  snapshot : id={validation.snapshot_id} count={validation.snapshot_count}")

    if not validation.valid:
        print(f"\nRefused: {validation.reason}")
        return 1

    reason = args.reason
    if not reason:
        try:
            reason = input("\nBusiness reason for this rollback: ").strip()
        except EOFError:
            reason = ""
    if not reason:
        print("A reason is required. Aborted.")
        return 1

    if not args.dry_run:
        confirm = input(f"\nType the table name to confirm rollback ({args.fqn}): ").strip()
        if confirm != args.fqn:
            print("Confirmation did not match table name. Aborted.")
            return 1

    actor = getpass.getuser()
    ok = rollback_metadata(args.fqn, target, actor=actor, reason=reason, dry_run=args.dry_run)

    if ok:
        print(f"\n{'DRY RUN — no changes made' if args.dry_run else 'Rollback complete'}: "
              f"{args.fqn} -> {target}")
        return 0
    print("\nRollback failed — see logs.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
