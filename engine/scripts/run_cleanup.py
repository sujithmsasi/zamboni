"""
Zamboni — Cleanup Entry Point (ZAMBONI-NONPROD-CLEANUP)
Executes hard deletion for PENDING_DROP tables whose 48h window has expired.
Run weekly — Sunday 04:00 UTC.

Usage:
    python -m engine.scripts.run_cleanup
    python -m engine.scripts.run_cleanup --environment preprod
    python -m engine.scripts.run_cleanup --dry-run
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
    """Zamboni Cleanup — permanently delete expired PENDING_DROP tables."""
    log.info("run_cleanup.start", environment=environment, dry_run=dry_run)

    if dry_run:
        click.echo("⚠  DRY RUN MODE — no tables will be deleted")

    engine = LifecycleEngine(dry_run=dry_run)
    result = engine.run_cleanup(environment=environment)

    click.echo("\n── Cleanup Summary ─────────────────────────────")
    click.echo(f"  Run ID     : {result['run_id']}")
    click.echo(f"  Environment: {environment}")
    click.echo(f"  Processed  : {result['tables_processed']}")
    click.echo(f"  Deleted    : {result['succeeded']}")
    click.echo(f"  Skipped    : {result['skipped']}")
    click.echo(f"  Failed     : {result['failed']}")
    click.echo(f"  Duration   : {result['elapsed_seconds']}s")
    click.echo("────────────────────────────────────────────────\n")

    sys.exit(1 if result["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
