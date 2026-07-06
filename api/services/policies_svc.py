"""
Zamboni API -- policies service (contracts.md §6 routers/policies.py).

Lifts the View/Edit/Bulk Apply/Templates query and mutation patterns from
app/pages/3_Policy_Configuration.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.settings import HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
from engine.core.config import apply_template, get_hk_config, get_policy_templates, reload_templates
from engine.utils.athena_client import read_sql, run_query

_TEMPLATES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "policy_templates.json"


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


# ── list / get ────────────────────────────────────────────────────────────────

def list_policies(
    page: int, size: int, domain: str | None = None, layer: str | None = None, tier: str | None = None,
) -> tuple[list[dict], int]:
    conditions = ["r.table_format = 'iceberg'"]
    if domain:
        conditions.append(f"r.domain = '{_esc(domain)}'")
    if layer:
        conditions.append(f"r.layer = '{_esc(layer)}'")
    if tier:
        conditions.append(f"r.tier = '{_esc(tier)}'")
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
    sets = []
    for key, value in fields.items():
        if value is None:
            continue
        if key == "window_config":
            sets.append(f"window_config = '{_esc(json.dumps(value))}'")
        elif key == "override_notes":
            sets.append(f"override_notes = '{_esc(value)}'")
        elif key in _UPDATABLE_FIELDS:
            if isinstance(value, bool):
                sets.append(f"{key} = {1 if value else 0}")
            elif isinstance(value, int):
                sets.append(f"{key} = {value}")
            else:
                sets.append(f"{key} = '{_esc(str(value))}'")
    if not sets:
        return False
    sets.append("manually_overridden = 1")
    sets.append(f"updated_at = '{_now()}'")
    sql = f"UPDATE {HK_CONFIG_TABLE} SET {', '.join(sets)} WHERE table_fqn = '{_esc(fqn)}'"
    run_query(sql, workgroup="app", dry_run=dry_run)
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


def apply_template_bulk(name: str, domain: str | None, layer: str | None, tier: str | None, dry_run: bool) -> int:
    conditions = ["hk_enabled = 1", "table_format = 'iceberg'"]
    if domain:
        conditions.append(f"domain = '{_esc(domain)}'")
    if layer:
        conditions.append(f"layer = '{_esc(layer)}'")
    if tier:
        conditions.append(f"tier = '{_esc(tier)}'")
    where = "WHERE " + " AND ".join(conditions)

    df = read_sql(f"SELECT table_fqn FROM {STREAM_REGISTRY_TABLE} {where}", workgroup="app")
    if df.empty:
        return 0

    count = 0
    for fqn in df["table_fqn"].tolist():
        apply_template(table_fqn=fqn, template_name=name, dry_run=dry_run)
        count += 1
    return count
