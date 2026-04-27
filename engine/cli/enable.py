"""
Zamboni — CLI: Enable / Disable HK
Flip hk_enabled for a table, domain, layer, or tier.
Always dry-run by default — use --no-dry-run to write.

Usage:
    python -m engine.cli.enable --table glue_catalog.finance_db.finance_staging
    python -m engine.cli.enable --domain finance --layer staging
    python -m engine.cli.enable --domain finance --tier critical
    python -m engine.cli.enable --table glue_catalog.finance_db.finance_staging --disable
    python -m engine.cli.enable --domain finance --dry-run-until 2026-06-01
"""
import sys
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config.settings import VALID_LAYERS, VALID_TIERS, DRY_RUN_DEFAULT, STREAM_REGISTRY_TABLE
from engine.utils.logger import get_logger

log     = get_logger(__name__)
console = Console()


@click.command()
@click.option("--table",    default=None, help="Single table FQN")
@click.option("--domain",   default=None, help="Enable all tables in a domain")
@click.option("--layer",    default=None, type=click.Choice(VALID_LAYERS), help="Filter by layer")
@click.option("--tier",     default=None, type=click.Choice(VALID_TIERS),  help="Filter by tier")
@click.option("--env",      default="prod", show_default=True)
@click.option("--disable",  is_flag=True,  default=False, help="Disable instead of enable")
@click.option("--dry-run-until", default=None,
              help="Set dry_run_until date (YYYY-MM-DD) instead of enabling")
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT)
def main(table, domain, layer, tier, env, disable, dry_run_until, dry_run):
    """Enable or disable HK Engine for tables."""
    from engine.utils.athena_client import read_sql, run_query
    from engine.core.registry import enable_hk, disable_hk, get_table, set_dry_run_until

    action = "DISABLE" if disable else ("DRY-RUN-UNTIL" if dry_run_until else "ENABLE")

    # ── Resolve target tables ─────────────────────────────────────────────────
    if table:
        row = get_table(table)
        if not row:
            console.print(f"[red]✗ Table not found in registry:[/] {table}")
            sys.exit(1)
        targets = [row]
    elif domain:
        conditions = [
            f"domain = '{domain}'",
            f"environment = '{env}'",
            "table_format = 'iceberg'",
        ]
        if layer: conditions.append(f"layer = '{layer}'")
        if tier:  conditions.append(f"tier = '{tier}'")
        where = "WHERE " + " AND ".join(conditions)
        sql   = f"SELECT table_fqn, domain, layer, tier, hk_enabled FROM {STREAM_REGISTRY_TABLE} {where}"
        df    = read_sql(sql, workgroup="app")
        targets = df.to_dict(orient="records") if not df.empty else []
    else:
        console.print("[red]✗ Provide --table or --domain[/]")
        sys.exit(1)

    if not targets:
        console.print("[yellow]No tables found matching the criteria.[/]")
        return

    console.print(Panel(
        f"[bold]Zamboni — {action}[/]\n"
        f"Tables  : [white]{len(targets)}[/]\n"
        f"Action  : [cyan]{action}[/]\n"
        f"Dry Run : {'[yellow]YES[/]' if dry_run else '[green]NO — writing[/]'}",
        title=f"{'🔴' if disable else '✅'} {action}",
    ))

    # Show preview
    preview = Table(show_header=True, header_style="bold blue")
    preview.add_column("Table", style="cyan", no_wrap=True, max_width=55)
    preview.add_column("Layer")
    preview.add_column("Tier")
    preview.add_column("Currently")
    preview.add_column("After")

    for t in targets[:20]:
        current = "[green]enabled[/]" if t.get("hk_enabled") else "[red]disabled[/]"
        after   = (
            "[red]disabled[/]"     if disable else
            f"[yellow]dry-run until {dry_run_until}[/]" if dry_run_until else
            "[green]enabled[/]"
        )
        preview.add_row(
            str(t.get("table_fqn","")),
            str(t.get("layer","")),
            str(t.get("tier","")),
            current, after,
        )

    if len(targets) > 20:
        preview.add_row(f"[dim]... and {len(targets)-20} more[/]", "", "", "", "")

    console.print(preview)

    if not dry_run:
        confirm = click.confirm(f"\n{action} {len(targets)} table(s)?", default=False)
        if not confirm:
            console.print("[yellow]Aborted.[/]")
            return

    # ── Execute ───────────────────────────────────────────────────────────────
    succeeded = 0
    failed    = 0

    for t in targets:
        fqn = t.get("table_fqn","")
        try:
            if dry_run_until:
                set_dry_run_until(fqn, dry_run_until, dry_run=dry_run)
            elif disable:
                disable_hk(fqn, reason="manual CLI disable", dry_run=dry_run)
            else:
                enable_hk(fqn, dry_run=dry_run)
            succeeded += 1
        except Exception as e:
            console.print(f"[red]✗ {fqn}:[/] {e}")
            failed += 1
            log.error("cli.enable.error", table_fqn=fqn, error=str(e))

    console.print(
        f"\n[green]✓ {action} complete:[/] "
        f"{succeeded} succeeded, {failed} failed"
        + (" [dim](dry run)[/]" if dry_run else "")
    )

    if not dry_run and not disable and not dry_run_until:
        console.print(f"\n[dim]Tip — validate before first real run:[/]")
        console.print(f"  [cyan]python -m engine.cli.dry_run --domain {domain or table}[/]")


if __name__ == "__main__":
    main()
