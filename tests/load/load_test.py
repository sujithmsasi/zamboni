"""
Zamboni — Load Test
Simulates a large-scale HK Engine run against dev AWS environment.
Tests throughput, error handling, and circuit breaker behaviour
at scale (100-1000 tables).

Run with:
    python tests/load/load_test.py --tables 100 --domain finance
    python tests/load/load_test.py --tables 1000 --all-domains

Requirements:
    - Dev AWS environment set up
    - Stream registry populated with tables
    - DRY_RUN_DEFAULT=true in .env (load test never writes)
"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TimeElapsedColumn
from rich.table import Table

from engine.core import circuit_breaker
from engine.core.config import get_hk_config
from engine.core.registry import get_enabled_tables
from engine.core.window_evaluator import EXECUTE, evaluate
from engine.utils.logger import get_logger

log     = get_logger(__name__)
console = Console()


@click.command()
@click.option("--tables",      default=100, show_default=True,
              help="Number of tables to simulate")
@click.option("--domain",      default=None, help="Filter by domain")
@click.option("--concurrency", default=10, show_default=True,
              help="Number of concurrent table evaluations")
@click.option("--env",         default="dev", show_default=True)
def main(tables, domain, concurrency, env):
    """
    Load test — simulate HK Engine evaluation at scale.
    Always runs in dry-run mode. Never writes to any AWS service.
    """
    console.print("\n[bold blue]🔥 Zamboni Load Test[/]")
    console.print(f"   Tables      : {tables}")
    console.print(f"   Domain      : {domain or 'all'}")
    console.print(f"   Concurrency : {concurrency}")
    console.print(f"   Environment : {env}")
    console.print("   Mode        : [yellow]DRY RUN (always)[/]\n")

    # ── Fetch tables ──────────────────────────────────────────────────────────
    start_fetch = time.perf_counter()
    all_tables  = get_enabled_tables(environment=env, domain=domain)
    fetch_time  = time.perf_counter() - start_fetch

    if not all_tables:
        console.print(f"[yellow]No enabled tables found in {env}.[/]")
        return

    # Limit to requested count
    target_tables = all_tables[:tables]
    console.print(
        f"[green]✓ Fetched {len(all_tables):,} tables in {fetch_time:.2f}s[/] "
        f"— testing {len(target_tables):,}\n"
    )

    # ── Run concurrent evaluation ─────────────────────────────────────────────
    results   = []
    durations = []

    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Evaluating tables...", total=len(target_tables))

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_evaluate_one, t): t
                for t in target_tables
            }

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                durations.append(result["duration_ms"])
                progress.advance(task)

    # ── Results ───────────────────────────────────────────────────────────────
    total     = len(results)
    would_run = sum(1 for r in results if r["verdict"] == "WOULD_RUN")
    skipped   = sum(1 for r in results if r["verdict"].startswith("SKIP"))
    no_config = sum(1 for r in results if r["verdict"] == "NO_CONFIG")
    errors    = sum(1 for r in results if r["verdict"] == "ERROR")

    avg_ms    = statistics.mean(durations) if durations else 0
    p50_ms    = statistics.median(durations) if durations else 0
    p90_ms    = statistics.quantiles(durations, n=10)[-1] if len(durations) >= 10 else max(durations, default=0)
    p99_ms    = statistics.quantiles(durations, n=100)[-1] if len(durations) >= 100 else max(durations, default=0)

    # Summary table
    summary = Table(show_header=True, header_style="bold blue", title="Load Test Results")
    summary.add_column("Metric",    style="white")
    summary.add_column("Value",     style="cyan", justify="right")

    summary.add_row("Total Tables",     f"{total:,}")
    summary.add_row("Would Run",        f"[green]{would_run:,}[/] ({would_run/total*100:.1f}%)")
    summary.add_row("Skipped",          f"[yellow]{skipped:,}[/]")
    summary.add_row("No Config",        f"[yellow]{no_config:,}[/]")
    summary.add_row("Errors",           f"[red]{errors:,}[/]")
    summary.add_row("─" * 20,          "─" * 10)
    summary.add_row("Avg Eval Time",    f"{avg_ms:.1f}ms")
    summary.add_row("p50 Eval Time",    f"{p50_ms:.1f}ms")
    summary.add_row("p90 Eval Time",    f"{p90_ms:.1f}ms")
    summary.add_row("p99 Eval Time",    f"{p99_ms:.1f}ms")
    summary.add_row("Registry Fetch",   f"{fetch_time*1000:.0f}ms")
    summary.add_row("Throughput",       f"{total/max(sum(durations)/1000, 0.001):.0f} tables/sec (concurrent)")

    console.print(summary)

    # Verdict distribution
    verdict_counts: dict[str, int] = {}
    for r in results:
        v = r["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    console.print("\n[bold]Verdict Distribution:[/]")
    for verdict, count in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        color = "green" if verdict == "WOULD_RUN" else "yellow" if "SKIP" in verdict else "red"
        bar   = "█" * min(40, int(count / total * 40))
        console.print(f"  [{color}]{verdict:30s}[/] {bar} {count:>5,}")

    # Slowest tables
    slowest = sorted(results, key=lambda r: r["duration_ms"], reverse=True)[:5]
    if slowest:
        console.print("\n[bold]Slowest 5 Evaluations:[/]")
        slow_t = Table(show_header=True, header_style="bold")
        slow_t.add_column("Table",    style="cyan", max_width=60)
        slow_t.add_column("Duration", justify="right")
        slow_t.add_column("Verdict")
        for r in slowest:
            slow_t.add_row(r["table_fqn"], f"{r['duration_ms']:.1f}ms", r["verdict"])
        console.print(slow_t)

    # Pass/fail
    if errors > 0 or no_config > total * 0.5:
        console.print(f"\n[red]⚠  Load test completed with concerns — {errors} errors, {no_config} missing configs[/]")
    else:
        console.print(f"\n[green]✓ Load test passed — {would_run:,}/{total:,} tables would run[/]")


def _evaluate_one(table_row: dict) -> dict:
    """Evaluate a single table through all gates. Returns timing + verdict."""
    fqn   = table_row.get("table_fqn", "")
    start = time.perf_counter()

    try:
        cfg = get_hk_config(fqn)
        if not cfg:
            return _result(fqn, "NO_CONFIG", start)

        # Gate 2 — Window
        decision = evaluate(cfg.get("window_config", ""), force=table_row.get("force_run", False))
        if decision != EXECUTE:
            return _result(fqn, f"SKIP_{decision}", start)

        # Gate 3 — Circuit breaker
        cb = circuit_breaker.check(fqn)
        if cb == circuit_breaker.OPEN:
            return _result(fqn, "SKIP_CIRCUIT_OPEN", start)

        return _result(fqn, "WOULD_RUN", start)

    except Exception as e:
        log.error("load_test.error", table_fqn=fqn, error=str(e))
        return _result(fqn, "ERROR", start, error=str(e))


def _result(fqn: str, verdict: str, start: float, error: str = "") -> dict:
    return {
        "table_fqn":   fqn,
        "verdict":     verdict,
        "duration_ms": (time.perf_counter() - start) * 1000,
        "error":       error,
    }


if __name__ == "__main__":
    main()
