"""
Zamboni — Lifecycle Scan Entry Point (ZAMBONI-NONPROD-SCAN)
Discovers all non-prod tables and updates the nonprod_registry.
Run weekly — Saturday 02:00 UTC.

Usage:
    python -m engine.scripts.run_lifecycle_scan
    python -m engine.scripts.run_lifecycle_scan --environment dev
    python -m engine.scripts.run_lifecycle_scan --dry-run
"""
import sys
import click
from config.settings import DRY_RUN_DEFAULT
from engine.engines.lifecycle_engine import LifecycleEngine
from engine.utils.logger import get_logger

log = get_logger(__name__)


@click.command()
@click.option("--environment", default="preprod", show_default=True,
              help="Non-prod environment to scan (preprod | dev | test)")
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT)
def main(environment, dry_run):
    """Zamboni Lifecycle Scan — discover and register non-prod tables."""
    log.info("run_lifecycle_scan.start", environment=environment, dry_run=dry_run)

    if dry_run:
        click.echo("⚠  DRY RUN MODE")

    engine = LifecycleEngine(dry_run=dry_run)
    result = engine.run_scan(environment=environment)

    click.echo("\n── Lifecycle Scan Summary ──────────────────────")
    click.echo(f"  Run ID     : {result['run_id']}")
    click.echo(f"  Environment: {environment}")
    click.echo(f"  Discovered : {result.get('discovered', 0)}")
    click.echo(f"  Errors     : {result['failed']}")
    click.echo(f"  Duration   : {result['elapsed_seconds']}s")
    click.echo("────────────────────────────────────────────────\n")

    sys.exit(1 if result["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
