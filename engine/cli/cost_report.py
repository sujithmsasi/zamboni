"""
Zamboni — CLI: Cost Report
Per-domain Athena scan cost + archival storage savings from execution_log.

Usage:
    python -m engine.cli.cost_report
    python -m engine.cli.cost_report --domain finance
    python -m engine.cli.cost_report --days 90
    python -m engine.cli.cost_report --export cost_report.csv
"""
import sys
from datetime import date

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import EXECUTION_LOG_TABLE
from engine.utils.logger import get_logger

log     = get_logger(__name__)
console = Console()

# Athena pricing: $5.00 per TB scanned (us-west-2)
ATHENA_COST_PER_TB = 5.0


@click.command()
@click.option("--domain", default=None, help="Filter by domain")
@click.option("--days",   default=30, show_default=True, help="Look-back window in days")
@click.option("--export", default=None, help="Export results to CSV file path")
def main(domain, days, export):
    """Show per-domain Athena cost and archival storage savings."""
    from engine.utils.athena_client import read_sql

    domain_clause = f"AND domain = '{domain}'" if domain else ""

    # ── Per-domain cost summary ───────────────────────────────────────────────
    sql = f"""
        SELECT
            domain,
            COUNT(DISTINCT table_fqn)                               AS tables,
            COUNT(*)                                                 AS operations,
            ROUND(SUM(bytes_scanned) / 1e9, 1)                      AS gb_scanned,
            ROUND(SUM(bytes_scanned) / 1e12 * {ATHENA_COST_PER_TB}, 4) AS athena_cost_usd,
            ROUND(SUM(bytes_rewritten) / 1e9, 1)                    AS gb_compacted,
            ROUND(SUM(bytes_archived) / 1e9, 1)                     AS gb_archived,
            SUM(snapshots_expired)                                   AS snapshots_expired,
            SUM(orphan_files_deleted)                                AS orphans_deleted
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND status IN ('SUCCESS', 'DRY_RUN')
          {domain_clause}
        GROUP BY domain
        ORDER BY athena_cost_usd DESC
    """

    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        console.print(f"[red]✗ Query failed:[/] {e}")
        sys.exit(1)

    if df.empty:
        console.print(f"[yellow]No execution data in the last {days} days.[/]")
        return

    # Totals
    total_cost     = float(df["athena_cost_usd"].sum())
    total_archived = float(df["gb_archived"].sum())
    total_compacted= float(df["gb_compacted"].sum())
    total_snaps    = int(df["snapshots_expired"].sum())

    console.print(Panel(
        f"[bold]Zamboni Cost Report[/]\n"
        f"Period   : Last [cyan]{days}[/] days  ({date.today()})\n"
        f"Domain   : [cyan]{domain or 'all'}[/]\n\n"
        f"Athena Cost   : [yellow]${total_cost:.4f}[/] USD\n"
        f"GB Archived   : [green]{total_archived:.1f} GB[/] moved to S3 Intelligent-Tiering\n"
        f"GB Compacted  : [green]{total_compacted:.1f} GB[/] rewritten\n"
        f"Snaps Expired : [green]{total_snaps:,}[/]",
        title="💰 Cost Report",
    ))

    # ── Per-domain table ──────────────────────────────────────────────────────
    t = Table(show_header=True, header_style="bold blue", show_lines=False)
    t.add_column("Domain",         style="cyan")
    t.add_column("Tables",         justify="right")
    t.add_column("Operations",     justify="right")
    t.add_column("GB Scanned",     justify="right")
    t.add_column("Athena Cost $",  justify="right")
    t.add_column("GB Archived",    justify="right")
    t.add_column("GB Compacted",   justify="right")
    t.add_column("Snaps Expired",  justify="right")

    for _, row in df.iterrows():
        cost = float(row.get("athena_cost_usd") or 0)
        cost_color = "red" if cost > 1.0 else "yellow" if cost > 0.1 else "green"
        t.add_row(
            str(row["domain"]),
            str(int(row.get("tables", 0))),
            str(int(row.get("operations", 0))),
            f"{float(row.get('gb_scanned', 0)):.1f}",
            f"[{cost_color}]${cost:.4f}[/]",
            f"{float(row.get('gb_archived', 0)):.1f}",
            f"{float(row.get('gb_compacted', 0)):.1f}",
            f"{int(row.get('snapshots_expired', 0)):,}",
        )

    console.print(t)

    # ── Top tables by cost ────────────────────────────────────────────────────
    console.print("\n[bold]Top 10 Tables by Athena Scan Cost[/]\n")

    top_sql = f"""
        SELECT
            table_fqn, domain, layer,
            ROUND(SUM(bytes_scanned) / 1e12 * {ATHENA_COST_PER_TB}, 4) AS cost_usd,
            ROUND(SUM(bytes_scanned) / 1e9, 1)                         AS gb_scanned,
            COUNT(*)                                                     AS operations
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND status = 'SUCCESS'
          AND bytes_scanned > 0
          {domain_clause}
        GROUP BY table_fqn, domain, layer
        ORDER BY cost_usd DESC
        LIMIT 10
    """

    try:
        top_df = read_sql(top_sql, workgroup="app")
        if not top_df.empty:
            top_t = Table(show_header=True, header_style="bold blue")
            top_t.add_column("Table",       style="cyan", max_width=50)
            top_t.add_column("Domain")
            top_t.add_column("Layer")
            top_t.add_column("Cost $",      justify="right")
            top_t.add_column("GB Scanned",  justify="right")
            top_t.add_column("Ops",         justify="right")

            for _, row in top_df.iterrows():
                top_t.add_row(
                    str(row["table_fqn"]),
                    str(row["domain"]),
                    str(row["layer"]),
                    f"${float(row.get('cost_usd',0)):.4f}",
                    f"{float(row.get('gb_scanned',0)):.1f}",
                    str(int(row.get("operations",0))),
                )
            console.print(top_t)
    except Exception as e:
        console.print(f"[dim]Top tables query failed: {e}[/]")

    # ── Export ────────────────────────────────────────────────────────────────
    if export:
        try:
            df.to_csv(export, index=False)
            console.print(f"\n[green]✓ Exported to:[/] [cyan]{export}[/]")
        except Exception as e:
            console.print(f"[red]✗ Export failed:[/] {e}")

    console.print(
        f"\n[dim]Note: Athena cost = GB_scanned / 1000 × ${ATHENA_COST_PER_TB:.2f} (us-west-2 rate)[/]"
    )


if __name__ == "__main__":
    main()
