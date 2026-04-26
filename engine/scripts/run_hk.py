"""
Zamboni — HK Engine Entry Point
Called by Control-M via SSH or SSM Run Command.

Usage:
    # Process all enabled tables
    python -m engine.scripts.run_hk

    # Process a single table
    python -m engine.scripts.run_hk --table glue_catalog.finance_db.finance_staging

    # Process all tables in a domain
    python -m engine.scripts.run_hk --domain finance

    # Process all tables in a domain + layer
    python -m engine.scripts.run_hk --domain finance --layer staging

    # Dry run (no writes — safe to test)
    python -m engine.scripts.run_hk --domain finance --dry-run

    # Force run (override safe window)
    python -m engine.scripts.run_hk --domain finance --force
"""
import sys
import click
from config.settings import DRY_RUN_DEFAULT
from engine.engines.hk_engine import HKEngine
from engine.utils.logger import get_logger

log = get_logger(__name__)


@click.command()
@click.option("--table",       default=None, help="Process a single table (FQN)")
@click.option("--domain",      default=None, help="Filter by domain")
@click.option("--layer",       default=None, help="Filter by layer (staging|datalake|base|master)")
@click.option("--tier",        default=None, help="Filter by tier (critical|standard|low)")
@click.option("--environment", default="prod", show_default=True, help="Target environment")
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT,
              help="Dry run — evaluate but do not execute (default: from .env)")
def main(table, domain, layer, tier, environment, dry_run):
    """Zamboni HK Engine — compaction, snapshot expiry, orphan cleanup."""

    log.info(
        "run_hk.start",
        table=table,
        domain=domain,
        layer=layer,
        tier=tier,
        environment=environment,
        dry_run=dry_run,
    )

    if dry_run:
        click.echo("⚠  DRY RUN MODE — no writes will be made")

    engine = HKEngine(dry_run=dry_run)

    result = engine.run(
        table_fqn=table,
        domain=domain,
        layer=layer,
        tier=tier,
        environment=environment,
    )

    # Print summary
    click.echo("\n── HK Engine Run Summary ──────────────────────")
    click.echo(f"  Run ID     : {result['run_id']}")
    click.echo(f"  Dry Run    : {result['dry_run']}")
    click.echo(f"  Processed  : {result['tables_processed']}")
    click.echo(f"  Succeeded  : {result['succeeded']}")
    click.echo(f"  Skipped    : {result['skipped']}")
    click.echo(f"  Failed     : {result['failed']}")
    click.echo(f"  Duration   : {result['elapsed_seconds']}s")
    click.echo("───────────────────────────────────────────────\n")

    if result["failed"] > 0:
        log.error("run_hk.completed_with_failures", **result)
        sys.exit(1)

    log.info("run_hk.completed", **result)
    sys.exit(0)


if __name__ == "__main__":
    main()
