"""
Zamboni — HK Config
Read hk_config per table, apply policy templates, upsert configs.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from config.settings import HK_CONFIG_TABLE, SNAPSHOT_MIN_FLOOR
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger

log = get_logger(__name__)

# Load policy templates once at module import
_TEMPLATES_PATH = Path(__file__).parent.parent.parent / "config" / "policy_templates.json"
_TEMPLATES: dict = {}


def _load_templates(force_reload: bool = False) -> dict:
    """Load policy templates from JSON. Cache-busted on writes."""
    global _TEMPLATES
    if not _TEMPLATES or force_reload:
        with open(_TEMPLATES_PATH) as f:
            data = json.load(f)
        # Strip comment keys — only keep dict entries
        _TEMPLATES = {
            k: v for k, v in data.items()
            if not k.startswith("_") and isinstance(v, dict)
        }
    return _TEMPLATES


def reload_templates() -> dict:
    """Force reload templates from disk — call after any write to policy_templates.json."""
    return _load_templates(force_reload=True)


def get_policy_templates() -> dict:
    """Return all available policy templates (dict of name -> config)."""
    return _load_templates()


# ══════════════════════════════════════════════════════════════════════════════
#  READ
# ══════════════════════════════════════════════════════════════════════════════

def get_hk_config(table_fqn: str) -> dict | None:
    """Return hk_config row for a table, or None if not configured."""
    sql = f"""
        SELECT * FROM {HK_CONFIG_TABLE}
        WHERE table_fqn = '{table_fqn}'
        LIMIT 1
    """
    df = read_sql(sql, workgroup="app")
    if df.empty:
        return None
    config = df.iloc[0].to_dict()

    # Enforce hard floor on snapshot_min_to_keep
    if config.get("snapshot_min_to_keep") and config["snapshot_min_to_keep"] < SNAPSHOT_MIN_FLOOR:
        log.warning(
            "config.snapshot_floor_enforced",
            table_fqn=table_fqn,
            configured=config["snapshot_min_to_keep"],
            enforced=SNAPSHOT_MIN_FLOOR,
        )
        config["snapshot_min_to_keep"] = SNAPSHOT_MIN_FLOOR

    return config





def get_template(template_name: str) -> dict | None:
    """Return a single policy template or None."""
    return _load_templates().get(template_name)


def infer_template(layer: str, tier: str) -> str:
    """
    Infer the best matching policy template given a layer and tier.
    Used during auto-registration when no template is explicitly specified.
    """
    mapping = {
        ("staging",  "critical"): "CRITICAL_HIGH_VOL",
        ("staging",  "standard"): "STAGING_DEFAULT",
        ("staging",  "low"):      "STAGING_DEFAULT",
        ("datalake", "critical"): "CRITICAL_HIGH_VOL",
        ("datalake", "standard"): "DATALAKE_DEFAULT",
        ("datalake", "low"):      "DATALAKE_DEFAULT",
        ("base",     "critical"): "BASE_SCD2",
        ("base",     "standard"): "BASE_SCD2",
        ("base",     "low"):      "BASE_SCD2",
        ("master",   "critical"): "MASTER_DEFAULT",
        ("master",   "standard"): "MASTER_DEFAULT",
        ("master",   "low"):      "MASTER_DEFAULT",
    }
    return mapping.get((layer, tier), "STAGING_DEFAULT")


# ══════════════════════════════════════════════════════════════════════════════
#  WRITE
# ══════════════════════════════════════════════════════════════════════════════

def apply_template(
    table_fqn: str,
    template_name: str,
    partition_column: str | None = "partition_date",
    partition_filter_days: int | None = None,
    sort_columns: list[str] | None = None,
    glue_job_name: str | None = None,
    dry_run: bool = False,
) -> bool:
    """
    Apply a policy template to a table.
    Creates or replaces the hk_config row.
    Enforces SNAPSHOT_MIN_FLOOR regardless of template value.
    """
    templates = _load_templates()
    if template_name not in templates:
        raise ValueError(
            f"Unknown template '{template_name}'. "
            f"Available: {list(templates.keys())}"
        )

    t   = templates[template_name]
    now = _now()

    # Enforce snapshot floor
    snap_min = max(t.get("snapshot_min_to_keep", SNAPSHOT_MIN_FLOOR), SNAPSHOT_MIN_FLOOR)

    # Sort columns as SQL array literal
    sort_arr = _to_sql_array(sort_columns)

    # Window config as JSON string
    window_json = json.dumps(t.get("window_config", {})).replace("'", "''")

    # Delete existing config first (Iceberg doesn't support true UPSERT easily)
    _delete_hk_config(table_fqn, dry_run=dry_run)

    orphan_cadence = t.get("orphan_cleanup_cadence_days", 7)
    # Gate flags from template (with safe defaults)
    gate1 = int(t.get("gate1_enabled", 0))  # Default OFF — ControlM not ready
    gate2 = int(t.get("gate2_enabled", 1))  # Default ON  — blackout window
    gate3 = int(t.get("gate3_enabled", 1))  # Default ON  — circuit breaker

    sql = f"""
        INSERT INTO {HK_CONFIG_TABLE} (
            table_fqn, policy_template, compaction_strategy,
            compaction_target_file_size_mb, compaction_engine,
            sort_order_cols, snapshot_retention_days, snapshot_min_to_keep,
            orphan_file_retention_days, orphan_cleanup_cadence_days,
            run_frequency, partition_column, partition_filter_days,
            window_config, gate1_enabled, gate2_enabled, gate3_enabled,
            manually_overridden, override_notes,
            created_at, updated_at
        ) VALUES (
            '{table_fqn}',
            '{template_name}',
            '{t["compaction_strategy"]}',
            {t["compaction_target_file_size_mb"]},
            '{t["compaction_engine"]}',
            {sort_arr},
            {t["snapshot_retention_days"]},
            {snap_min},
            {t["orphan_file_retention_days"]},
            {orphan_cadence},
            '{t["run_frequency"]}',
            {f"'{partition_column}'" if partition_column else "NULL"},
            {partition_filter_days if partition_filter_days else "NULL"},
            '{window_json}',
            {gate1}, {gate2}, {gate3},
            0,
            NULL,
            '{now}',
            '{now}'
        )
    """

    log.info(
        "config.apply_template",
        table_fqn=table_fqn,
        template=template_name,
        dry_run=dry_run,
    )
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def update_config_field(
    table_fqn: str,
    field: str,
    value,
    override_notes: str = "",
    dry_run: bool = False,
) -> bool:
    """
    Update a single field in hk_config.
    Marks manually_overridden = true.
    """
    now         = _now()
    value_sql   = f"'{value}'" if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else str(value)
    notes_sql   = f"'{_esc(override_notes)}'" if override_notes else "override_notes"

    sql = f"""
        UPDATE {HK_CONFIG_TABLE}
        SET {field}             = {value_sql},
            manually_overridden = 1,
            override_notes      = {notes_sql},
            updated_at          = '{now}'
        WHERE table_fqn = '{table_fqn}'
    """
    log.info("config.update_field", table_fqn=table_fqn, field=field, dry_run=dry_run)
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def _delete_hk_config(table_fqn: str, dry_run: bool = False) -> None:
    """Delete existing hk_config row — called before INSERT during apply_template."""
    sql = f"DELETE FROM {HK_CONFIG_TABLE} WHERE table_fqn = '{table_fqn}'"
    run_query(sql, workgroup="app", dry_run=dry_run)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _to_sql_array(items: list[str] | None) -> str:
    """Convert a Python list to a SQL ARRAY literal, or NULL."""
    if not items:
        return "NULL"
    quoted = ", ".join(f"'{i}'" for i in items)
    return f"ARRAY[{quoted}]"
