"""
Zamboni — CLI: Table Registration
Discover Iceberg tables from Glue catalog, generate YAML manifest,
and bulk-insert into stream_registry + hk_config.

Commands:
    discover  — scan a Glue database and output a YAML manifest
    bulk      — register all tables from a YAML manifest
    single    — register a single table interactively
    status    — show registration status for a database

Usage:
    python -m engine.cli.register discover --db finance_db --out finance.yaml
    python -m engine.cli.register bulk --manifest finance.yaml
    python -m engine.cli.register single --table glue_catalog.finance_db.finance_staging
    python -m engine.cli.register status --db finance_db
"""
import sys
from datetime import date

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import DRY_RUN_DEFAULT, VALID_ENVIRONMENTS, VALID_LAYERS, VALID_TIERS
from engine.utils.glue_client import get_tables, guess_partition_column, is_iceberg_table
from engine.utils.logger import get_logger

log = get_logger(__name__)
console = Console()


@click.group()
def cli():
    """Zamboni Table Registration — onboard tables into the stream registry."""
    pass


# ── discover ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--db",     required=True, help="Glue database name to scan")
@click.option("--out",    required=True, help="Output YAML manifest file path")
@click.option("--domain", default=None,  help="Domain name (inferred from db name if not set)")
@click.option("--env",    default="prod", show_default=True,
              type=click.Choice(VALID_ENVIRONMENTS), help="Environment")
def discover(db, out, domain, env):
    """Scan a Glue database and generate a YAML manifest for review."""
    console.print(f"\n[bold blue]🔍 Scanning:[/] [cyan]{db}[/]\n")

    try:
        # Always a fresh, live scan -- this is a deliberate, occasional CLI
        # action (not the interactive Browse & Register page the 24h cache in
        # glue_client.py exists to protect), and the whole point of `discover`
        # is showing exactly what's really in Glue right now.
        tables = get_tables(db, force_refresh=True)
    except Exception as e:
        console.print(f"[red]✗ Failed to scan database:[/] {e}")
        sys.exit(1)

    iceberg_tables = [t for t in tables if is_iceberg_table(t)]
    other_tables   = [t for t in tables if not is_iceberg_table(t)]

    console.print(f"  Found [green]{len(iceberg_tables)}[/] Iceberg tables")
    console.print(f"  Found [yellow]{len(other_tables)}[/] non-Iceberg tables (will be skipped)\n")

    if not iceberg_tables:
        console.print("[yellow]No Iceberg tables found. Nothing to register.[/]")
        return

    inferred_domain = domain or _infer_domain(db)
    inferred_layer  = _infer_layer(db)

    manifest = {
        "meta": {
            "database":    db,
            "domain":      inferred_domain,
            "environment": env,
            "generated":   str(date.today()),
            "total_tables": len(iceberg_tables),
        },
        "tables": []
    }

    for table in iceberg_tables:
        name = table["Name"]
        fqn  = f"glue_catalog.{db}.{name}"
        # Best-effort real guess from the table's own Glue columns (already
        # fetched above, no extra call) instead of always assuming
        # "partition_date" -- still just a starting point for the human
        # review step this manifest exists for, not a guarantee.
        columns = table.get("StorageDescriptor", {}).get("Columns", [])
        guess   = guess_partition_column(columns)
        manifest["tables"].append({
            "table_fqn":             fqn,
            "domain":                inferred_domain,
            "layer":                 inferred_layer or "staging",
            "tier":                  "standard",
            "environment":           env,
            "table_format":          "iceberg",
            "owner_email":           "",
            "ci_number":             "",
            "hk_enabled":            False,
            "archive_enabled":       False,
            "archive_retention_days": 30,
            "policy_template":       "STAGING_DEFAULT",
            "partition_column":      guess["partition_column"] if guess else "partition_date",
            "notes":                 "",
        })

    with open(out, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    console.print(f"[green]✓ Manifest written to:[/] [cyan]{out}[/]")
    console.print("\n[dim]Next steps:[/]")
    console.print(f"  1. Review and edit [cyan]{out}[/]")
    console.print("     — Set correct layer, tier, owner_email, ci_number per table")
    console.print(f"  2. Run: [cyan]python -m engine.cli.register bulk --manifest {out}[/]")


# ── bulk ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--manifest", required=True, help="YAML manifest file path")
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT, help="Simulate without writing")
def bulk(manifest, dry_run):
    """Register all tables from a YAML manifest into stream_registry + hk_config."""
    try:
        with open(manifest) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]✗ Failed to load manifest:[/] {e}")
        sys.exit(1)

    tables = data.get("tables", [])
    meta   = data.get("meta", {})

    console.print(Panel(
        f"[bold]Zamboni — Bulk Registration[/]\n"
        f"Database  : {meta.get('database', '—')}\n"
        f"Domain    : {meta.get('domain', '—')}\n"
        f"Tables    : {len(tables)}\n"
        f"Dry Run   : {'[yellow]YES[/]' if dry_run else '[green]NO — writing to registry[/]'}",
        title="📋 Bulk Register",
    ))

    if not dry_run:
        confirm = click.confirm(f"\nRegister {len(tables)} tables into stream_registry?", default=False)
        if not confirm:
            console.print("[yellow]Aborted.[/]")
            return

    from engine.core.config import apply_template
    from engine.core.registry import register_table, table_exists

    succeeded = 0
    skipped   = 0
    failed    = 0

    rich_table = Table(show_header=True, header_style="bold blue")
    rich_table.add_column("Table", style="cyan", no_wrap=True)
    rich_table.add_column("Layer")
    rich_table.add_column("Tier")
    rich_table.add_column("Status")

    for t in tables:
        fqn = t["table_fqn"]
        try:
            if table_exists(fqn):
                rich_table.add_row(fqn, t["layer"], t["tier"], "[yellow]SKIP (already registered)[/]")
                skipped += 1
                continue

            register_table(
                table_fqn=fqn,
                domain=t["domain"],
                layer=t["layer"],
                tier=t["tier"],
                environment=t["environment"],
                table_format=t.get("table_format", "iceberg"),
                owner_email=t.get("owner_email", ""),
                ci_number=t.get("ci_number", ""),
                hk_enabled=t.get("hk_enabled", False),
                archive_enabled=t.get("archive_enabled", False),
                archive_retention_days=t.get("archive_retention_days"),
                registered_by="cli",
                notes=t.get("notes", ""),
                dry_run=dry_run,
            )

            # Apply policy template
            template = t.get("policy_template", "STAGING_DEFAULT")
            apply_template(
                table_fqn=fqn,
                template_name=template,
                partition_column=t.get("partition_column", "partition_date"),
                dry_run=dry_run,
            )

            rich_table.add_row(
                fqn, t["layer"], t["tier"],
                "[dim]DRY RUN[/]" if dry_run else "[green]✓ REGISTERED[/]"
            )
            succeeded += 1

        except Exception as e:
            rich_table.add_row(fqn, t.get("layer",""), t.get("tier",""), f"[red]✗ {e}[/]")
            failed += 1
            log.error("cli.register.bulk.error", table_fqn=fqn, error=str(e))

    console.print(rich_table)
    console.print(
        f"\n[green]✓ Registered: {succeeded}[/]  "
        f"[yellow]Skipped: {skipped}[/]  "
        f"[red]Failed: {failed}[/]"
        + (" [dim](dry run)[/]" if dry_run else "")
    )


# ── single ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--table",    required=True, help="Table FQN (glue_catalog.db.table)")
@click.option("--domain",   required=True, help="Business domain")
@click.option("--layer",    required=True, type=click.Choice(VALID_LAYERS))
@click.option("--tier",     default="standard", type=click.Choice(VALID_TIERS))
@click.option("--env",      default="prod", type=click.Choice(VALID_ENVIRONMENTS))
@click.option("--owner",    default="", help="Owner email")
@click.option("--ci",       default="", help="CI number")
@click.option("--template", default=None, help="Policy template (auto-inferred if not set)")
@click.option("--dry-run/--no-dry-run", default=DRY_RUN_DEFAULT)
def single(table, domain, layer, tier, env, owner, ci, template, dry_run):
    """Register a single table into stream_registry."""
    from engine.core.config import apply_template, infer_template
    from engine.core.registry import register_table, table_exists

    console.print(f"\n[bold blue]Registering:[/] [cyan]{table}[/]")

    if table_exists(table):
        console.print("[yellow]⚠  Already registered.[/] Use the Table Registration page to update config.")
        return

    tmpl = template or infer_template(layer, tier)

    try:
        register_table(
            table_fqn=table, domain=domain, layer=layer, tier=tier,
            environment=env, owner_email=owner, ci_number=ci,
            registered_by="cli", dry_run=dry_run,
        )
        apply_template(table_fqn=table, template_name=tmpl, dry_run=dry_run)

        console.print(
            f"[green]✓ Registered[/] with template [cyan]{tmpl}[/]"
            + (" [dim](dry run)[/]" if dry_run else "")
        )
        console.print(f"\n  Next: [cyan]python -m engine.cli.dry_run --table {table}[/]")
    except Exception as e:
        console.print(f"[red]✗ Registration failed:[/] {e}")
        sys.exit(1)


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--db", required=True, help="Glue database name")
def status(db):
    """Show registration status for all tables in a Glue database."""
    from config.settings import STREAM_REGISTRY_TABLE
    from engine.utils.athena_client import read_sql

    try:
        glue_tables = get_tables(db, force_refresh=True)
        iceberg     = [f"glue_catalog.{db}.{t['Name']}" for t in glue_tables if is_iceberg_table(t)]
        total       = len(iceberg)

        if not total:
            console.print(f"[yellow]No Iceberg tables found in {db}[/]")
            return

        fqns_str = "', '".join(iceberg[:500])
        sql = f"""
            SELECT table_fqn, hk_enabled, dry_run_until, tier, registered_at
            FROM {STREAM_REGISTRY_TABLE}
            WHERE table_fqn IN ('{fqns_str}')
        """
        df          = read_sql(sql, workgroup="app")
        registered  = set(df["table_fqn"].tolist()) if not df.empty else set()
        unregistered= [t for t in iceberg if t not in registered]

        console.print(f"\n[bold]Registration Status — [cyan]{db}[/][/]\n")
        console.print(f"  Total Iceberg : [white]{total}[/]")
        console.print(f"  Registered    : [green]{len(registered)}[/]")
        console.print(f"  Unregistered  : [yellow]{len(unregistered)}[/]")

        if unregistered:
            console.print("\n[yellow]Unregistered tables:[/]")
            for t in unregistered[:20]:
                console.print(f"  [dim]•[/] {t}")
            if len(unregistered) > 20:
                console.print(f"  [dim]... and {len(unregistered)-20} more[/]")

    except Exception as e:
        console.print(f"[red]✗ Status check failed:[/] {e}")
        sys.exit(1)


# ── helpers ───────────────────────────────────────────────────────────────────

def _infer_domain(db: str) -> str:
    for suffix in ["_preprod", "_dev", "_test", "_staging", "_prod"]:
        if db.endswith(suffix):
            return db[:-len(suffix)]
    return db.split("_")[0] if "_" in db else db


def _infer_layer(db: str) -> str:
    db_lower = db.lower()
    for layer in ["staging", "datalake", "base", "master"]:
        if layer in db_lower:
            return layer
    return "staging"


if __name__ == "__main__":
    cli()
