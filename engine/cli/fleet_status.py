"""
Zamboni — CLI: Fleet Status
Coverage metrics, health summary, never-housekept tables.

Usage:
    python -m engine.cli.fleet_status
    python -m engine.cli.fleet_status --domain finance
    python -m engine.cli.fleet_status coverage
    python -m engine.cli.fleet_status health --domain finance
    python -m engine.cli.fleet_status stale --days 14
"""
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import EXECUTION_LOG_TABLE, STREAM_REGISTRY_TABLE
from engine.utils.logger import get_logger

log     = get_logger(__name__)
console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Zamboni Fleet Status — coverage, health, and stale table reports."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(coverage)


# ── coverage ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--domain", default=None, help="Filter by domain")
@click.option("--env",    default="prod", show_default=True)
def coverage(domain, env):
    """Show HK coverage % by domain and layer."""
    from engine.utils.athena_client import read_sql

    domain_clause = f"AND domain = '{domain}'" if domain else ""
    sql = f"""
        SELECT
            domain, layer,
            COUNT(*)                                                AS total,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END)     AS enabled,
            SUM(CASE WHEN dry_run_until >= CURRENT_DATE THEN 1 ELSE 0 END) AS in_dry_run,
            SUM(CASE WHEN table_format != 'iceberg' THEN 1 ELSE 0 END) AS non_iceberg,
            ROUND(
                SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
            )                                                       AS pct_enabled
        FROM {STREAM_REGISTRY_TABLE}
        WHERE environment = '{env}'
        {domain_clause}
        GROUP BY domain, layer
        ORDER BY domain, layer
    """

    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        console.print(f"[red]✗ Query failed:[/] {e}")
        sys.exit(1)

    if df.empty:
        console.print(f"[yellow]No tables registered in {env}.[/]")
        return

    total_all   = int(df["total"].sum())
    enabled_all = int(df["enabled"].sum())
    pct_all     = round(enabled_all / total_all * 100, 1) if total_all > 0 else 0

    console.print(Panel(
        f"[bold]Fleet Coverage — {env.upper()}[/]\n"
        f"Total Registered : [white]{total_all:,}[/]\n"
        f"HK Enabled       : [green]{enabled_all:,}[/] ({pct_all}%)\n"
        f"Non-Iceberg      : [yellow]{int(df['non_iceberg'].sum()):,}[/]",
        title="📊 Coverage Summary",
    ))

    t = Table(show_header=True, header_style="bold blue")
    t.add_column("Domain",       style="cyan")
    t.add_column("Layer",        style="white")
    t.add_column("Total",        justify="right")
    t.add_column("HK Enabled",   justify="right")
    t.add_column("In Dry-Run",   justify="right")
    t.add_column("Coverage %",   justify="right")

    for _, row in df.iterrows():
        pct  = float(row["pct_enabled"] or 0)
        pct_color = "green" if pct == 100 else "yellow" if pct >= 50 else "red"
        t.add_row(
            str(row["domain"]),
            str(row["layer"]),
            str(int(row["total"])),
            str(int(row["enabled"])),
            str(int(row["in_dry_run"])),
            f"[{pct_color}]{pct}%[/]",
        )

    console.print(t)


# ── health ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--domain", default=None, help="Filter by domain")
@click.option("--env",    default="prod", show_default=True)
@click.option("--days",   default=7, show_default=True, help="Look-back window in days")
def health(domain, env, days):
    """Show engine execution health — successes, failures, skips."""
    from engine.utils.athena_client import read_sql

    domain_clause = f"AND domain = '{domain}'" if domain else ""
    sql = f"""
        SELECT
            engine, operation, status,
            COUNT(*)                        AS count,
            SUM(snapshots_expired)          AS snapshots_expired,
            SUM(orphan_files_deleted)       AS orphans_deleted,
            ROUND(SUM(bytes_rewritten)/1e9,2) AS gb_rewritten
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
        {domain_clause}
        GROUP BY engine, operation, status
        ORDER BY engine, operation, status
    """

    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        console.print(f"[red]✗ Query failed:[/] {e}")
        sys.exit(1)

    if df.empty:
        console.print(f"[yellow]No execution data in the last {days} days.[/]")
        return

    console.print(f"\n[bold]Execution Health — last {days} days[/]\n")

    t = Table(show_header=True, header_style="bold blue")
    t.add_column("Engine",    style="cyan")
    t.add_column("Operation", style="white")
    t.add_column("Status")
    t.add_column("Count",     justify="right")
    t.add_column("Snaps Expired", justify="right")
    t.add_column("Orphans",       justify="right")
    t.add_column("GB Rewritten",  justify="right")

    status_colors = {
        "SUCCESS": "green", "FAILURE": "red",
        "SKIPPED": "yellow", "DRY_RUN": "blue",
    }

    for _, row in df.iterrows():
        status     = str(row["status"])
        color      = status_colors.get(status, "white")
        t.add_row(
            str(row["engine"]),
            str(row["operation"]),
            f"[{color}]{status}[/]",
            str(int(row["count"])),
            str(int(row.get("snapshots_expired") or 0)),
            str(int(row.get("orphans_deleted") or 0)),
            str(row.get("gb_rewritten") or "0"),
        )

    console.print(t)


# ── stale ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--domain", default=None,   help="Filter by domain")
@click.option("--days",   default=14, show_default=True,
              help="Flag tables not housekept in last N days")
@click.option("--env",    default="prod", show_default=True)
def stale(domain, days, env):
    """List enabled tables that have not been successfully housekept recently."""
    from engine.utils.athena_client import read_sql

    domain_clause = f"AND r.domain = '{domain}'" if domain else ""
    sql = f"""
        SELECT
            r.table_fqn, r.domain, r.layer, r.tier,
            MAX(l.completed_at) AS last_hk
        FROM {STREAM_REGISTRY_TABLE} r
        LEFT JOIN {EXECUTION_LOG_TABLE} l
            ON  r.table_fqn = l.table_fqn
            AND l.status    = 'SUCCESS'
            AND l.execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
        WHERE r.hk_enabled    = true
          AND r.table_format  = 'iceberg'
          AND r.environment   = '{env}'
          {domain_clause}
        GROUP BY r.table_fqn, r.domain, r.layer, r.tier
        HAVING MAX(l.completed_at) IS NULL
        ORDER BY r.tier, r.domain, r.layer
        LIMIT 100
    """

    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        console.print(f"[red]✗ Query failed:[/] {e}")
        sys.exit(1)

    if df.empty:
        console.print(f"[green]✓ All enabled tables have been housekept in the last {days} days.[/]")
        return

    console.print(f"\n[yellow]⚠  {len(df)} tables not housekept in {days}+ days:[/]\n")

    t = Table(show_header=True, header_style="bold blue")
    t.add_column("Table",  style="cyan", no_wrap=True, max_width=55)
    t.add_column("Domain", style="white")
    t.add_column("Layer")
    t.add_column("Tier")
    t.add_column("Last HK")

    tier_colors = {"critical": "red", "standard": "yellow", "low": "dim"}

    for _, row in df.iterrows():
        tier   = str(row["tier"])
        color  = tier_colors.get(tier, "white")
        last   = str(row["last_hk"])[:19] if row["last_hk"] else "[red]Never[/]"
        t.add_row(
            str(row["table_fqn"]),
            str(row["domain"]),
            str(row["layer"]),
            f"[{color}]{tier}[/]",
            last,
        )

    console.print(t)
    console.print("\n[dim]Tip: Run engine for these tables:[/]")
    console.print(f"  [cyan]python -m engine.scripts.run_hk --domain {domain or '<domain>'} --dry-run[/]")


if __name__ == "__main__":
    cli()
