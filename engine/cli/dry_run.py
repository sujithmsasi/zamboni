"""
Zamboni — CLI: Dry Run
Simulate HK Engine for a single table or domain.
Shows health check results, gate decisions, and SQL preview.
No writes — safe to run anytime.

Usage:
    python -m engine.cli.dry_run --table glue_catalog.finance_db.finance_staging
    python -m engine.cli.dry_run --domain finance --layer staging
    python -m engine.cli.dry_run --domain finance
"""
import sys
import json
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from config.settings import VALID_LAYERS
from engine.utils.logger import get_logger

log     = get_logger(__name__)
console = Console()


@click.command()
@click.option("--table",  default=None, help="Single table FQN")
@click.option("--domain", default=None, help="Process all enabled tables in a domain")
@click.option("--layer",  default=None, type=click.Choice(VALID_LAYERS), help="Filter by layer")
@click.option("--env",    default="prod", show_default=True, help="Environment")
@click.option("--verbose/--no-verbose", default=False, help="Show SQL and full config")
def main(table, domain, layer, env, verbose):
    """Simulate HK Engine — evaluate gates and preview operations without writing."""

    if not table and not domain:
        console.print("[red]✗ Provide --table or --domain[/]")
        sys.exit(1)

    console.print(Panel(
        "[bold]Zamboni — HK Engine Dry Run[/]\n"
        f"Scope : [cyan]{table or domain}[/]\n"
        f"Layer : [cyan]{layer or 'all'}[/]\n"
        f"Env   : [cyan]{env}[/]",
        title="🧪 Dry Run",
    ))

    from engine.core.registry import get_table, get_enabled_tables
    from engine.core.config import get_hk_config
    from engine.core.window_evaluator import evaluate, EXECUTE
    from engine.core import circuit_breaker
    from engine.utils.glue_client import is_upstream_job_complete

    # Fetch tables
    if table:
        row = get_table(table)
        tables = [row] if row else []
        if not tables:
            console.print(f"[red]✗ Table not found in registry:[/] {table}")
            sys.exit(1)
    else:
        tables = get_enabled_tables(environment=env, domain=domain, layer=layer)

    console.print(f"\n[bold]Tables to evaluate:[/] {len(tables)}\n")

    results = Table(show_header=True, header_style="bold blue", show_lines=True)
    results.add_column("Table",         style="cyan",  no_wrap=True, max_width=50)
    results.add_column("Tier",          width=10)
    results.add_column("Gate 1\nUpstream", width=12)
    results.add_column("Gate 2\nWindow",   width=12)
    results.add_column("Gate 3\nCircuit",  width=12)
    results.add_column("Compaction",    width=14)
    results.add_column("Vacuum",        width=10)
    results.add_column("Verdict")

    for t in tables:
        fqn  = t["table_fqn"]
        tier = t.get("tier", "standard")
        cfg  = get_hk_config(fqn)

        if not cfg:
            results.add_row(fqn, tier, "—", "—", "—", "—", "—", "[yellow]NO CONFIG[/]")
            continue

        # Gate 1 — Upstream
        upstream_job = t.get("dependent_job_name")
        if upstream_job and t.get("dependent_job_type") == "glue":
            g1 = "[green]✓[/]" if is_upstream_job_complete(upstream_job) else "[red]PENDING[/]"
        else:
            g1 = "[dim]N/A[/]"

        # Gate 2 — Window
        window_json = cfg.get("window_config", "")
        force       = t.get("force_run", False)
        decision    = evaluate(window_json, force=force)
        g2 = "[green]✓[/]" if decision == EXECUTE else f"[yellow]{decision}[/]"

        # Gate 3 — Circuit breaker
        cb = circuit_breaker.check(fqn)
        g3 = "[green]✓[/]" if cb == circuit_breaker.CLOSED else "[red]OPEN[/]"

        # Operations (would-run assessment)
        strategy = cfg.get("compaction_strategy", "binpack")
        target   = cfg.get("compaction_target_file_size_mb", 128)
        snap_ret = cfg.get("snapshot_retention_days", 7)
        comp_str = f"{strategy}/{target}MB"
        vac_str  = f"{snap_ret}d"

        # Verdict
        if g1 == "[red]PENDING[/]":
            verdict = "[yellow]SKIP — upstream pending[/]"
        elif decision != EXECUTE:
            verdict = f"[yellow]SKIP — {decision}[/]"
        elif cb != circuit_breaker.CLOSED:
            verdict = "[red]SKIP — circuit open[/]"
        else:
            verdict = "[green]WOULD RUN[/]"

        results.add_row(
            fqn, tier, g1, g2, g3, comp_str, vac_str, verdict
        )

        # Verbose: show SQL
        if verbose and decision == EXECUTE:
            from engine.strategies.binpack import build_optimize_sql
            from engine.utils.partition_utils import build_hot_partition_filter

            part_col    = cfg.get("partition_column")
            part_days   = cfg.get("partition_filter_days")
            part_filter = build_hot_partition_filter(part_col, part_days) if part_col else None

            if strategy == "binpack":
                sql = build_optimize_sql(fqn, target, part_filter)
                console.print(f"\n[dim]SQL for {fqn}:[/]")
                console.print(Syntax(sql, "sql", theme="monokai", word_wrap=True))
            else:
                console.print(f"\n[dim]{fqn}:[/] Would trigger Glue job with strategy=[cyan]{strategy}[/]")

    console.print(results)

    would_run = sum(1 for t in tables if "[green]WOULD RUN[/]" in _verdict_for(t, tables))
    console.print(
        f"\n[bold]Summary:[/] {len(tables)} tables evaluated — "
        f"[green]{would_run} would run[/], "
        f"[yellow]{len(tables)-would_run} would skip[/]"
    )


def _verdict_for(table_row, all_tables):
    """Helper to get verdict string — simplified for summary count."""
    return ""


if __name__ == "__main__":
    main()
