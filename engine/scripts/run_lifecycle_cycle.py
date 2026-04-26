"""
Zamboni — Lifecycle Cycle Entry Point (ZAMBONI-NONPROD-LIFECYCLE)
Evaluates state transitions and sends GREENZONE / PENDING_DROP notifications.
Run weekly — Saturday 03:00 UTC (after scan completes).

Usage:
    python -m engine.scripts.run_lifecycle_cycle
    python -m engine.scripts.run_lifecycle_cycle --environment preprod
    python -m engine.scripts.run_lifecycle_cycle --dry-run
"""
import sys
import click
from config.settings import DRY_RUN_DEFAULT
from engine.engines.lifecycle_engine import LifecycleEngine
from engine.utils.logger import get_logger

log = get_logger(__name__)


@click.command()
@click.option("--environment", default="preprod", show_default=True)
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT)
def main(environment, dry_run):
    """Zamboni Lifecycle Cycle — evaluate states and send notifications."""
    log.info("run_lifecycle_cycle.start", environment=environment, dry_run=dry_run)

    if dry_run:
        click.echo("⚠  DRY RUN MODE — no state changes or notifications will be sent")

    engine = LifecycleEngine(dry_run=dry_run)
    result = engine.run(environment=environment)

    click.echo("\n── Lifecycle Cycle Summary ─────────────────────")
    click.echo(f"  Run ID       : {result['run_id']}")
    click.echo(f"  Environment  : {environment}")
    click.echo(f"  Processed    : {result['tables_processed']}")
    click.echo(f"  Transitioned : {result.get('transitioned', 0)}")
    click.echo(f"  Notified     : {result.get('notified', 0)}")
    click.echo(f"  Skipped      : {result['skipped']}")
    click.echo(f"  Errors       : {result['failed']}")
    click.echo(f"  Duration     : {result['elapsed_seconds']}s")
    click.echo("────────────────────────────────────────────────\n")

    sys.exit(1 if result["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
