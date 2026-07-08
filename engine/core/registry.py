"""
Zamboni — Registry
Read/write operations for stream_registry and domain_registry.
All queries go through athena_client — no direct boto3 calls here.
"""
from __future__ import annotations

from datetime import UTC, datetime

from config.settings import (
    DOMAIN_REGISTRY_TABLE,
    STREAM_REGISTRY_TABLE,
    VALID_ENVIRONMENTS,
    VALID_LAYERS,
    VALID_TIERS,
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


def get_domain(domain_name: str) -> dict | None:
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
    is_active: bool = True,
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
    archive_int = 1 if archive_enabled else 0
    active_int = 1 if is_active else 0
    sql = f"""
        INSERT INTO {DOMAIN_REGISTRY_TABLE} (
            domain_name, display_name, description,
            owner_name, owner_email, team_name,
            archive_enabled, hot_retention_days, archive_duration_days,
            stale_threshold_days, auto_delete_after_days,
            is_active, environment,
            created_at, registered_by, updated_at, notes,
            digest_enabled, digest_email,
            registered_at
        ) VALUES (
            '{domain_name}',
            '{display_name}',
            '{_esc(description)}',
            '{_esc(owner_name)}',
            '{_esc(owner_email)}',
            '{_esc(team_name)}',
            {archive_int},
            {hot_retention_days},
            {archive_duration_days},
            {stale_threshold_days},
            {auto_delete_after_days},
            {active_int},
            '{environment}',
            '{now}',
            '{registered_by}',
            '{now}',
            '{_esc(notes)}',
            0,
            '',
            '{now}'
        )
    """
    log.info("registry.register_domain", domain=domain_name, dry_run=dry_run)
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  STREAM REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

def get_table(table_fqn: str) -> dict | None:
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
    layer: str | None = None,
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
    domain: str | None = None,
    tier: str | None = None,
    layer: str | None = None,
) -> list[dict]:
    """
    Return tables eligible for HK Engine processing.

    Includes TWO groups:
      A) Tables with hk_enabled=true — fully enabled
      B) Tables with hk_enabled=false BUT dry_run_until >= today — ramp-up
         evaluation: gates run, operations log DRY_RUN instead of executing.

    Use is_in_dry_run_ramp(row) in the engine to distinguish group B.

    Filters: table_format=iceberg, environment, domain, tier, layer as given.
    """
    conditions = [
        f"environment = '{environment}'",
        "table_format = 'iceberg'",
        # Include both: fully enabled tables OR active dry_run_until ramp-up
        "(hk_enabled = true OR (dry_run_until IS NOT NULL AND dry_run_until >= CURRENT_DATE))",
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
    owner_email: str = "",
    ci_number: str = "",
    hk_enabled: bool = False,
    archive_enabled: bool = False,
    archive_retention_days: int | None = None,
    registered_by: str = "streamlit",
    notes: str = "",
    # Control-M integration fields
    controlm_pipeline_job: str | None = None,
    controlm_hk_job: str | None = None,
    dependent_on_controlm_job: str | None = None,
    controlm_job_start_time: str = "02:00",
    controlm_expected_duration_min: int = 0,
    dependent_job_type: str = "controlm",
    dry_run: bool = False,
) -> bool:
    """
    Register a new table in stream_registry.
    hk_enabled defaults to False — enable explicitly after dry-run validation.
    """
    _validate_layer(layer)
    _validate_tier(tier)
    _validate_environment(environment)

    now  = _now()
    arch = archive_retention_days if archive_retention_days else "NULL"

    if table_exists(table_fqn):
        # Table already registered — do an UPDATE instead of failing
        _gate1_job = dependent_on_controlm_job or controlm_pipeline_job or ""
        upd_sql = f"""
            UPDATE {STREAM_REGISTRY_TABLE}
            SET domain                         = '{domain}',
                layer                          = '{layer}',
                tier                           = '{tier}',
                table_format                   = '{table_format}',
                environment                    = '{environment}',
                owner_email                    = '{_esc(owner_email)}',
                ci_number                      = '{_esc(ci_number)}',
                controlm_pipeline_job          = '{_esc(controlm_pipeline_job or "")}',
                controlm_hk_job                = '{_esc(controlm_hk_job or "")}',
                dependent_on_controlm_job      = '{_esc(_gate1_job)}',
                dependent_job_type             = '{dependent_job_type}',
                controlm_job_start_time        = '{controlm_job_start_time}',
                controlm_expected_duration_min = {int(controlm_expected_duration_min)},
                registered_by                  = '{registered_by}',
                updated_at                     = '{now}'
            WHERE table_fqn = '{table_fqn}'
        """
        log.info("registry.update_table", table_fqn=table_fqn, domain=domain, dry_run=dry_run)
        run_query(upd_sql, workgroup="app", dry_run=dry_run)
        return True

    hk_int      = 1 if hk_enabled else 0
    archive_int = 1 if archive_enabled else 0
    sql = f"""
        INSERT INTO {STREAM_REGISTRY_TABLE} (
            table_fqn, domain, layer, tier,
            table_format, environment, owner_email, ci_number,
            hk_enabled, dry_run_until, force_run,
            dependent_job_name, dependent_job_type,
            controlm_pipeline_job, controlm_hk_job, dependent_on_controlm_job,
            controlm_job_start_time, controlm_expected_duration_min,
            archive_enabled, archive_retention_days, archive_bucket,
            lifecycle_enabled, processing_cadence,
            properties_synced, last_execution_id,
            registered_by, registered_at, updated_at,
            database_name, owner_name, notes
        ) VALUES (
            '{table_fqn}',
            '{domain}',
            '{layer}',
            '{tier}',
            '{table_format}',
            '{environment}',
            '{_esc(owner_email)}',
            '{_esc(ci_number)}',
            {hk_int},
            NULL,
            0,
            NULL,
            '{dependent_job_type}',
            {f"'{_esc(controlm_pipeline_job)}'" if controlm_pipeline_job else "NULL"},
            {f"'{_esc(controlm_hk_job)}'"       if controlm_hk_job       else "NULL"},
            {f"'{_esc(dependent_on_controlm_job or controlm_pipeline_job or '')}'" },
            '{controlm_job_start_time}',
            {int(controlm_expected_duration_min)},
            {archive_int},
            {arch},
            NULL,
            0,
            NULL,
            0,
            NULL,
            '{registered_by}',
            '{now}',
            '{now}',
            '{table_fqn.split(".")[1] if "." in table_fqn else ""}',
            '',
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


def get_archivable_tables(domain: str | None = None) -> list[dict]:
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
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


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
