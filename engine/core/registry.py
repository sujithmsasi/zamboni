"""
Zamboni — Registry
Read/write operations for stream_registry and domain_registry.
All queries go through athena_client — no direct boto3 calls here.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from config.settings import (
    STREAM_REGISTRY_TABLE,
    DOMAIN_REGISTRY_TABLE,
    VALID_LAYERS,
    VALID_TIERS,
    VALID_ENVIRONMENTS,
)
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  DOMAIN REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

def get_all_domains(active_only: bool = True) -> list[dict]:
    """Return all registered domains."""
    where = "WHERE is_active = true" if active_only else ""
    sql = f"SELECT * FROM {DOMAIN_REGISTRY_TABLE} {where} ORDER BY domain_name"
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records")


def get_domain(domain_name: str) -> Optional[dict]:
    """Return a single domain record or None."""
    sql = f"""
        SELECT * FROM {DOMAIN_REGISTRY_TABLE}
        WHERE domain_name = '{domain_name}'
        LIMIT 1
    """
    df = read_sql(sql, workgroup="app")
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def domain_exists(domain_name: str) -> bool:
    """Return True if domain is already registered."""
    return get_domain(domain_name) is not None


def register_domain(
    domain_name: str,
    display_name: str,
    description: str = "",
    owner_name: str = "",
    owner_email: str = "",
    team_name: str = "",
    archive_enabled: bool = False,
    hot_retention_days: int = 30,
    archive_duration_days: int = 365,
    stale_threshold_days: int = 60,
    auto_delete_after_days: int = 120,
    environment: str = "prod",
    registered_by: str = "streamlit",
    notes: str = "",
    dry_run: bool = False,
) -> bool:
    """
    Register a new domain. Returns True on success.
    Raises ValueError if domain already exists.
    """
    if domain_exists(domain_name):
        raise ValueError(f"Domain '{domain_name}' is already registered.")

    now = _now()
    sql = f"""
        INSERT INTO {DOMAIN_REGISTRY_TABLE} VALUES (
            '{domain_name}',
            '{display_name}',
            '{_esc(description)}',
            '{_esc(owner_name)}',
            '{_esc(owner_email)}',
            '{_esc(team_name)}',
            {str(archive_enabled).lower()},
            {hot_retention_days},
            {archive_duration_days},
            {stale_threshold_days},
            {auto_delete_after_days},
            true,
            '{environment}',
            TIMESTAMP '{now}',
            '{registered_by}',
            TIMESTAMP '{now}',
            '{_esc(notes)}'
        )
    """
    log.info("registry.register_domain", domain=domain_name, dry_run=dry_run)
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  STREAM REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

def get_table(table_fqn: str) -> Optional[dict]:
    """Return a single stream_registry row or None."""
    sql = f"""
        SELECT * FROM {STREAM_REGISTRY_TABLE}
        WHERE table_fqn = '{table_fqn}'
        LIMIT 1
    """
    df = read_sql(sql, workgroup="app")
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def table_exists(table_fqn: str) -> bool:
    return get_table(table_fqn) is not None


def get_tables_by_domain(
    domain: str,
    layer: Optional[str] = None,
    environment: str = "prod",
    hk_enabled_only: bool = False,
) -> list[dict]:
    """Return all tables for a domain, optionally filtered."""
    conditions = [
        f"domain = '{domain}'",
        f"environment = '{environment}'",
    ]
    if layer:
        conditions.append(f"layer = '{layer}'")
    if hk_enabled_only:
        conditions.append("hk_enabled = true")
        conditions.append("table_format = 'iceberg'")

    where = "WHERE " + " AND ".join(conditions)
    sql = f"SELECT * FROM {STREAM_REGISTRY_TABLE} {where} ORDER BY layer, table_fqn"
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records")


def get_tables_by_stream(stream_id: str) -> list[dict]:
    """Return all tables belonging to a logical stream."""
    sql = f"""
        SELECT * FROM {STREAM_REGISTRY_TABLE}
        WHERE stream_id = '{stream_id}'
        ORDER BY layer
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records")


def is_in_dry_run_ramp(table_row: dict) -> bool:
    """
    Return True if a table is still in its dry-run ramp-up window.
    Tables with dry_run_until >= today are evaluated through all gates
    but HK operations are logged as DRY_RUN rather than executed.
    Supports the ramp-up pattern: validate HK behaviour before going live.
    """
    from datetime import date as _date
    dry_run_until = table_row.get("dry_run_until")
    if dry_run_until is None:
        return False
    if isinstance(dry_run_until, str):
        try:
            dry_run_until = _date.fromisoformat(str(dry_run_until)[:10])
        except ValueError:
            return False
    if isinstance(dry_run_until, _date):
        return dry_run_until >= _date.today()
    return False


def get_enabled_tables(
    environment: str = "prod",
    domain: Optional[str] = None,
    tier: Optional[str] = None,
    layer: Optional[str] = None,
) -> list[dict]:
    """
    Return tables eligible for HK Engine processing.
    Includes dry_run_until tables so they are evaluated through all gates —
    the engine logs DRY_RUN instead of executing for ramp-up tables.
    Use is_in_dry_run_ramp(row) in the engine to check per table.

    Filters: hk_enabled=true, table_format=iceberg.
    """
    conditions = [
        f"environment = '{environment}'",
        "hk_enabled = true",
        "table_format = 'iceberg'",
    ]
    if domain:
        conditions.append(f"domain = '{domain}'")
    if tier:
        conditions.append(f"tier = '{tier}'")
    if layer:
        conditions.append(f"layer = '{layer}'")

    where = "WHERE " + " AND ".join(conditions)
    sql = f"""
        SELECT * FROM {STREAM_REGISTRY_TABLE}
        {where}
        ORDER BY
            CASE tier
                WHEN 'critical' THEN 1
                WHEN 'standard' THEN 2
                WHEN 'low'      THEN 3
                ELSE 4
            END,
            domain, layer, table_fqn
    """
    df = read_sql(sql, workgroup="app")
    log.info(
        "registry.get_enabled_tables",
        count=len(df),
        environment=environment,
        domain=domain,
        tier=tier,
    )
    return df.to_dict(orient="records")


def register_table(
    table_fqn: str,
    domain: str,
    layer: str,
    tier: str,
    environment: str = "prod",
    table_format: str = "iceberg",
    stream_id: Optional[str] = None,
    owner_email: str = "",
    ci_number: str = "",
    hk_enabled: bool = False,
    archive_enabled: bool = False,
    archive_retention_days: Optional[int] = None,
    registered_by: str = "streamlit",
    notes: str = "",
    dry_run: bool = False,
) -> bool:
    """
    Register a new table in stream_registry.
    hk_enabled defaults to False — enable explicitly after dry-run validation.
    """
    _validate_layer(layer)
    _validate_tier(tier)
    _validate_environment(environment)

    if table_exists(table_fqn):
        raise ValueError(f"Table '{table_fqn}' is already registered.")

    sid  = stream_id or _generate_stream_id(domain, layer)
    now  = _now()
    arch = archive_retention_days if archive_retention_days else "NULL"

    sql = f"""
        INSERT INTO {STREAM_REGISTRY_TABLE} VALUES (
            '{table_fqn}',
            '{sid}',
            '{domain}',
            '{layer}',
            '{tier}',
            '{table_format}',
            '{environment}',
            '{_esc(owner_email)}',
            '{_esc(ci_number)}',
            {str(hk_enabled).lower()},
            NULL,
            false,
            NULL,
            'none',
            NULL,
            NULL,
            {str(archive_enabled).lower()},
            {arch},
            NULL,
            false,
            TIMESTAMP '{now}',
            '{registered_by}',
            TIMESTAMP '{now}',
            '{_esc(notes)}'
        )
    """
    log.info("registry.register_table", table_fqn=table_fqn, domain=domain, dry_run=dry_run)
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def enable_hk(table_fqn: str, dry_run: bool = False) -> bool:
    """Enable HK Engine for a table."""
    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET hk_enabled = true,
            updated_at  = TIMESTAMP '{_now()}'
        WHERE table_fqn = '{table_fqn}'
    """
    log.info("registry.enable_hk", table_fqn=table_fqn, dry_run=dry_run)
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def disable_hk(table_fqn: str, reason: str = "", dry_run: bool = False) -> bool:
    """Disable HK Engine for a table (circuit breaker or manual)."""
    notes_append = f" | Auto-disabled: {reason}" if reason else ""
    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET hk_enabled = false,
            updated_at  = TIMESTAMP '{_now()}',
            notes       = COALESCE(notes, '') || '{_esc(notes_append)}'
        WHERE table_fqn = '{table_fqn}'
    """
    log.info("registry.disable_hk", table_fqn=table_fqn, reason=reason, dry_run=dry_run)
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def set_dry_run_until(table_fqn: str, until_date: str, dry_run: bool = False) -> bool:
    """Set dry_run_until date for a table (format: YYYY-MM-DD)."""
    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET dry_run_until = DATE '{until_date}',
            updated_at    = TIMESTAMP '{_now()}'
        WHERE table_fqn = '{table_fqn}'
    """
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def get_archivable_tables(domain: Optional[str] = None) -> list[dict]:
    """Return staging tables with archival enabled."""
    conditions = [
        "archive_enabled = true",
        "layer = 'staging'",
        "table_format = 'iceberg'",
        "environment = 'prod'",
    ]
    if domain:
        conditions.append(f"domain = '{domain}'")

    where = "WHERE " + " AND ".join(conditions)
    sql   = f"SELECT * FROM {STREAM_REGISTRY_TABLE} {where} ORDER BY domain, table_fqn"
    df    = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _esc(value: str) -> str:
    """Escape single quotes for SQL string literals."""
    return str(value).replace("'", "''")


def _validate_layer(layer: str) -> None:
    if layer not in VALID_LAYERS:
        raise ValueError(f"Invalid layer '{layer}'. Must be one of: {VALID_LAYERS}")


def _validate_tier(tier: str) -> None:
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier '{tier}'. Must be one of: {VALID_TIERS}")


def _validate_environment(env: str) -> None:
    if env not in VALID_ENVIRONMENTS:
        raise ValueError(f"Invalid environment '{env}'. Must be one of: {VALID_ENVIRONMENTS}")


def _generate_stream_id(domain: str, layer: str) -> str:
    """Generate a unique stream ID — STR-DOM-XXXX format."""
    suffix = str(uuid.uuid4())[:8].upper()
    return f"STR-{domain[:3].upper()}-{suffix}"
