"""
Zamboni — Archival Engine Entry Point
Called by Control-M weekly (Sunday 04:00 UTC).

Usage:
    # Run all archivable domains
    python -m engine.scripts.run_archival

    # Run a specific domain
    python -m engine.scripts.run_archival --domain finance

    # Dry run
    python -m engine.scripts.run_archival --domain finance --dry-run
"""
import sys

import click

from config.settings import DRY_RUN_DEFAULT
from engine.engines.archival_engine import ArchivalEngine
from engine.utils.logger import get_logger

log = get_logger(__name__)


@click.command()
@click.option("--domain",      default=None, help="Limit to a specific domain")
@click.option("--environment", default="prod", show_default=True)
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT)
def main(domain, environment, dry_run):
    """Zamboni Archival Engine — export cold staging partitions to S3."""

    log.info("run_archival.start", domain=domain, environment=environment, dry_run=dry_run)

    if dry_run:
        click.echo("⚠  DRY RUN MODE — no exports or deletes will be made")

    engine = ArchivalEngine(dry_run=dry_run)
    result = engine.run(domain=domain, environment=environment)

    click.echo("\n── Archival Engine Run Summary ────────────────")
    click.echo(f"  Run ID           : {result['run_id']}")
    click.echo(f"  Dry Run          : {result['dry_run']}")
    click.echo(f"  Tables Processed : {result['tables_processed']}")
    click.echo(f"  Partitions Found : {result.get('total_partitions', 0)}")
    click.echo(f"  Succeeded        : {result['succeeded']}")
    click.echo(f"  Skipped          : {result['skipped']}")
    click.echo(f"  Failed           : {result['failed']}")
    click.echo(f"  Duration         : {result['elapsed_seconds']}s")
    click.echo("───────────────────────────────────────────────\n")

    if result["failed"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
