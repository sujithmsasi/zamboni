"""
Zamboni API -- domains service.

> ADDED (Phase 4): contracts.md §6 never defined a domains router (Phase 2's
REALITY note only evaluated the 8 sections already specified there). The
DomainManagement page needs domain CRUD, so this phase adds GET/POST/PUT
/api/domains, lifted from app/pages/1_Domain_Management.py + engine/core/
registry.py -- same "lift, never duplicate" convention as the other *_svc
modules. See contracts.md §6 domains subsection for the added route list.
"""
from __future__ import annotations

from datetime import UTC, datetime

from config.settings import DOMAIN_REGISTRY_TABLE, STREAM_REGISTRY_TABLE
from engine.core import registry
from engine.utils.athena_client import read_sql, run_query


def _esc(value) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def list_domains(active_only: bool = False) -> list[dict]:
    where = "WHERE is_active = true" if active_only else ""
    df = read_sql(f"SELECT * FROM {DOMAIN_REGISTRY_TABLE} {where} ORDER BY domain_name", workgroup="app")
    domains = df.to_dict(orient="records")

    counts_df = read_sql(
        f"SELECT domain, COUNT(*) AS table_count FROM {STREAM_REGISTRY_TABLE} GROUP BY domain", workgroup="app"
    )
    counts = dict(zip(counts_df["domain"], counts_df["table_count"].astype(int))) if not counts_df.empty else {}
    for d in domains:
        d["table_count"] = int(counts.get(d["domain_name"], 0))
    return domains


def get_domain(domain_name: str) -> dict | None:
    return registry.get_domain(domain_name)


class DomainValidationError(ValueError):
    pass


def create_domain(fields: dict, actor: str, dry_run: bool) -> bool:
    domain_name = fields["domain_name"].strip().lower()
    if not domain_name or not fields.get("display_name", "").strip() or not fields.get("owner_email", "").strip():
        raise DomainValidationError("domain_name, display_name, and owner_email are required.")
    return registry.register_domain(
        domain_name=domain_name,
        display_name=fields["display_name"].strip(),
        description=fields.get("description", "").strip(),
        owner_name=fields.get("owner_name", "").strip(),
        owner_email=fields["owner_email"].strip(),
        team_name=fields.get("team_name", "").strip(),
        archive_enabled=bool(fields.get("archive_enabled", True)),
        hot_retention_days=int(fields.get("hot_retention_days", 30)),
        archive_duration_days=int(fields.get("archive_duration_days", 365)),
        stale_threshold_days=int(fields.get("stale_threshold_days", 60)),
        auto_delete_after_days=int(fields.get("auto_delete_after_days", 120)),
        is_active=bool(fields.get("is_active", True)),
        registered_by=f"api:{actor}",
        notes=fields.get("notes", "").strip(),
        dry_run=dry_run,
    )


# key -> SQL literal kind, mirrors app/pages/1_Domain_Management.py's edit form fields
_UPDATE_COLUMNS = {
    "display_name": "str", "owner_name": "str", "owner_email": "str", "team_name": "str",
    "ci_number": "str", "archive_enabled": "bool", "hot_retention_days": "int",
    "archive_duration_days": "int", "stale_threshold_days": "int", "auto_delete_after_days": "int",
    "is_active": "bool", "digest_enabled": "bool", "digest_email": "str", "notes": "str",
}


def update_domain(domain_name: str, fields: dict, dry_run: bool) -> bool:
    if registry.get_domain(domain_name) is None:
        raise DomainValidationError(f"Domain '{domain_name}' is not registered.")

    sets = []
    for key, kind in _UPDATE_COLUMNS.items():
        value = fields.get(key)
        if value is None:
            continue
        if kind == "bool":
            sets.append(f"{key} = {1 if value else 0}")
        elif kind == "int":
            sets.append(f"{key} = {int(value)}")
        else:
            sets.append(f"{key} = '{_esc(value)}'")

    if not sets:
        return False

    sets.append(f"updated_at = '{_now()}'")
    sql = f"UPDATE {DOMAIN_REGISTRY_TABLE} SET {', '.join(sets)} WHERE domain_name = '{_esc(domain_name)}'"
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True
