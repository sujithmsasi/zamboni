"""
Zamboni API -- policies service (contracts.md §6 routers/policies.py).

Lifts the View/Edit/Bulk Apply/Templates query and mutation patterns from
app/pages/3_Policy_Configuration.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.settings import HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
from engine.core.config import apply_template, get_hk_config, get_policy_templates, reload_templates
from engine.core.control_plane import read_sql, update_row

_TEMPLATES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "policy_templates.json"

# Mirrors 3_Policy_Configuration.py's tmpl_tab_del `_BUILTIN` set -- these
# templates ship with the app and can't be removed via the UI.
_BUILTIN_TEMPLATES = {
    "STAGING_DEFAULT", "DATALAKE_DEFAULT", "BASE_SCD2",
    "MASTER_DEFAULT", "CRITICAL_HIGH_VOL", "NON_PROD_DEFAULT",
}

_DEFAULT_WINDOW_CONFIG = {
    "type": "post_batch",
    "timezone": "America/Los_Angeles",
    "delay_minutes": 30,
    "duration_hours": 4,
    "blackout_hours": [6, 7, 8, 9, 18, 19, 20, 21],
}


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


# ── list / get ────────────────────────────────────────────────────────────────

def list_policies(
    page: int, size: int, domain: str | None = None, layer: str | None = None, tier: str | None = None,
    search: str | None = None,
) -> tuple[list[dict], int]:
    conditions = ["r.table_format = 'iceberg'"]
    if domain:
        conditions.append(f"r.domain = '{_esc(domain)}'")
    if layer:
        conditions.append(f"r.layer = '{_esc(layer)}'")
    if tier:
        conditions.append(f"r.tier = '{_esc(tier)}'")
    if search:
        conditions.append(f"r.table_fqn LIKE '%{_esc(search)}%'")
    where = "WHERE " + " AND ".join(conditions)

    base = f"FROM {STREAM_REGISTRY_TABLE} r LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn {where}"
    total_df = read_sql(f"SELECT COUNT(*) AS cnt {base}", workgroup="app")
    total = int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0

    offset = max(page - 1, 0) * size
    sql = f"""
        SELECT r.table_fqn, r.domain, r.layer, r.tier,
               c.policy_template, c.compaction_strategy, c.compaction_engine,
               c.compaction_target_file_size_mb, c.snapshot_retention_days,
               c.snapshot_min_to_keep, c.orphan_file_retention_days,
               c.run_frequency, c.gate1_enabled, c.gate2_enabled, c.gate3_enabled,
               c.manually_overridden
        {base}
        ORDER BY r.domain, r.layer, r.table_fqn
        LIMIT {int(size)} OFFSET {int(offset)}
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records"), total


def get_policy(fqn: str) -> dict | None:
    return get_hk_config(fqn)


# ── update ────────────────────────────────────────────────────────────────────

_UPDATABLE_FIELDS = {
    "gate1_enabled", "gate2_enabled", "gate3_enabled",
    "snapshot_retention_days", "snapshot_min_to_keep",
    "orphan_file_retention_days", "orphan_cleanup_cadence_days",
    "run_frequency", "compaction_strategy", "compaction_engine",
    "compaction_target_file_size_mb", "sort_order_cols",
    "partition_column", "partition_type",
}


def update_policy(fqn: str, fields: dict, dry_run: bool) -> bool:
    column_values: dict = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "window_config":
            # Pre-serialize to a JSON string here -- update_row()'s SQL
            # literal formatting only knows str/int/bool/None, not dicts.
            column_values["window_config"] = json.dumps(value)
        elif key == "override_notes":
            column_values["override_notes"] = value
        elif key in _UPDATABLE_FIELDS:
            column_values[key] = value

    if not column_values:
        return False
    column_values["manually_overridden"] = True
    update_row(HK_CONFIG_TABLE, "table_fqn", fqn, column_values, dry_run=dry_run)
    return True


# ── templates ─────────────────────────────────────────────────────────────────

def list_templates() -> dict:
    return get_policy_templates()


def update_template(name: str, fields: dict, dry_run: bool) -> bool:
    with open(_TEMPLATES_PATH) as f:
        all_templates = json.load(f)
    if name not in all_templates:
        raise ValueError(f"Unknown template '{name}'.")

    if dry_run:
        return True

    updates: dict[str, Any] = {k: v for k, v in fields.items() if v is not None and k != "dry_run"}
    all_templates[name].update(updates)

    with open(_TEMPLATES_PATH, "w") as f:
        json.dump(all_templates, f, indent=2)
    reload_templates()
    return True


def create_template(name: str, fields: dict, dry_run: bool) -> bool:
    """
    > ADDED (Phase 5a): contracts.md §6 policies router only locked GET/PUT
    for templates -- 3_Policy_Configuration.py's "Add Template" sub-tab has
    no contract endpoint yet. Same precedent as Phase 4's domains router:
    additive, same envelope/dry_run/audit conventions as every other route.
    """
    key = name.strip().upper()
    if not key:
        raise ValueError("Template name is required.")

    with open(_TEMPLATES_PATH) as f:
        all_templates = json.load(f)
    if key in all_templates:
        raise ValueError(f"Template '{key}' already exists.")

    if dry_run:
        return True

    all_templates[key] = {
        "description": fields.get("description") or "",
        "compaction_strategy": fields.get("compaction_strategy") or "binpack",
        "compaction_engine": fields.get("compaction_engine") or "athena",
        "compaction_target_file_size_mb": fields.get("compaction_target_file_size_mb") or 128,
        "snapshot_retention_days": fields.get("snapshot_retention_days") or 7,
        "snapshot_min_to_keep": fields.get("snapshot_min_to_keep") or 2,
        "orphan_file_retention_days": fields.get("orphan_file_retention_days") or 2,
        "run_frequency": fields.get("run_frequency") or "daily",
        "gate1_enabled": bool(fields.get("gate1_enabled", False)),
        "gate2_enabled": bool(fields.get("gate2_enabled", True)),
        "gate3_enabled": bool(fields.get("gate3_enabled", True)),
        "window_config": fields.get("window_config") or dict(_DEFAULT_WINDOW_CONFIG),
    }
    with open(_TEMPLATES_PATH, "w") as f:
        json.dump(all_templates, f, indent=2)
    reload_templates()
    return True


def count_template_usage(name: str) -> int:
    df = read_sql(
        f"SELECT COUNT(*) AS cnt FROM {HK_CONFIG_TABLE} WHERE policy_template = '{_esc(name)}'", workgroup="app",
    )
    return int(df.iloc[0]["cnt"]) if not df.empty else 0


def delete_template(name: str, dry_run: bool) -> bool:
    """> ADDED (Phase 5a) -- see create_template()'s note. Mirrors the
    Streamlit "Delete Template" sub-tab's built-in-protection + usage-count
    guard exactly."""
    if name in _BUILTIN_TEMPLATES:
        raise ValueError(f"Template '{name}' is built-in and cannot be deleted.")

    with open(_TEMPLATES_PATH) as f:
        all_templates = json.load(f)
    if name not in all_templates:
        raise ValueError(f"Unknown template '{name}'.")

    usage = count_template_usage(name)
    if usage > 0:
        raise ValueError(f"Template '{name}' is assigned to {usage} table(s); reassign them first.")

    if dry_run:
        return True

    del all_templates[name]
    with open(_TEMPLATES_PATH, "w") as f:
        json.dump(all_templates, f, indent=2)
    reload_templates()
    return True


def apply_template_bulk(
    name: str, domain: str | None, layer: str | None, tier: str | None, dry_run: bool,
    skip_overridden: bool = True,
) -> int:
    """
    skip_overridden mirrors 3_Policy_Configuration.py's "Override existing
    manual overrides" checkbox (default unchecked -> skip): a bulk template
    apply should not silently clobber a table someone deliberately
    hand-tuned unless explicitly told to. Requires a LEFT JOIN to hk_config
    since manually_overridden lives there, not on stream_registry.
    """
    conditions = ["r.hk_enabled = 1", "r.table_format = 'iceberg'"]
    if domain:
        conditions.append(f"r.domain = '{_esc(domain)}'")
    if layer:
        conditions.append(f"r.layer = '{_esc(layer)}'")
    if tier:
        conditions.append(f"r.tier = '{_esc(tier)}'")
    if skip_overridden:
        conditions.append("(c.manually_overridden IS NULL OR c.manually_overridden = 0)")
    where = "WHERE " + " AND ".join(conditions)

    df = read_sql(
        f"SELECT r.table_fqn FROM {STREAM_REGISTRY_TABLE} r "
        f"LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn {where}",
        workgroup="app",
    )
    if df.empty:
        return 0

    count = 0
    for fqn in df["table_fqn"].tolist():
        apply_template(table_fqn=fqn, template_name=name, dry_run=dry_run)
        count += 1
    return count
