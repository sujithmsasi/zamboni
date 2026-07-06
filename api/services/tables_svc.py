"""
Zamboni API -- tables service (contracts.md §6 routers/tables.py).

Lifts the table-registry query/mutation patterns from
app/pages/2_Table_Registration.py (browse/register/bulk Control-M/job-mapping
CSV import-export) into a reusable engine-layer function set the FastAPI
router calls -- the Streamlit page keeps using its own inline SQL this phase
(no page files touched).
"""
from __future__ import annotations

import io
from datetime import UTC, datetime

import pandas as pd

from config.settings import STREAM_REGISTRY_TABLE
from engine.core import registry
from engine.core.config import apply_template, infer_template
from engine.utils.athena_client import read_sql, run_query
from engine.utils.glue_client import get_databases, get_tables, is_iceberg_table


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


# ── list / get ────────────────────────────────────────────────────────────────

def list_tables(
    page: int, size: int, domain: str | None = None, layer: str | None = None,
    tier: str | None = None, env: str | None = None, search: str | None = None,
    database_name: str | None = None,
) -> tuple[list[dict], int]:
    conditions = []
    if domain:
        conditions.append(f"domain = '{_esc(domain)}'")
    if layer:
        conditions.append(f"layer = '{_esc(layer)}'")
    if tier:
        conditions.append(f"tier = '{_esc(tier)}'")
    if env:
        conditions.append(f"environment = '{_esc(env)}'")
    if search:
        conditions.append(f"table_fqn LIKE '%{_esc(search)}%'")
    if database_name:
        conditions.append(f"database_name = '{_esc(database_name)}'")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total_df = read_sql(f"SELECT COUNT(*) AS cnt FROM {STREAM_REGISTRY_TABLE} {where}", workgroup="app")
    total = int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0

    offset = max(page - 1, 0) * size
    sql = f"""
        SELECT * FROM {STREAM_REGISTRY_TABLE} {where}
        ORDER BY domain, layer, table_fqn
        LIMIT {int(size)} OFFSET {int(offset)}
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records"), total


def get_table(fqn: str) -> dict | None:
    return registry.get_table(fqn)


# ── register / update ────────────────────────────────────────────────────────

def register_table(req: dict, registered_by: str, dry_run: bool) -> dict:
    """
    Register + auto-infer/apply a policy template in one call -- mirrors
    2_Table_Registration.py's Browse & Register tab, which calls
    registry.register_table() then apply_template(infer_template(...)) for
    every selected row. Returns {"success", "template"} so the router/UI can
    show which template got applied (editable later in Policy Config).
    """
    ok = registry.register_table(
        table_fqn=req["table_fqn"], domain=req["domain"], layer=req["layer"], tier=req["tier"],
        environment=req.get("environment", "prod"), table_format=req.get("table_format", "iceberg"),
        stream_id=req.get("stream_id"), owner_email=req.get("owner_email", ""),
        ci_number=req.get("ci_number", ""), hk_enabled=req.get("hk_enabled", False),
        archive_enabled=req.get("archive_enabled", False),
        archive_retention_days=req.get("archive_retention_days"),
        registered_by=registered_by, notes=req.get("notes", ""),
        controlm_pipeline_job=req.get("controlm_pipeline_job"),
        controlm_hk_job=req.get("controlm_hk_job"),
        dependent_on_controlm_job=req.get("dependent_on_controlm_job"),
        controlm_job_start_time=req.get("controlm_job_start_time", "02:00"),
        controlm_expected_duration_min=req.get("controlm_expected_duration_min", 0),
        dependent_job_type=req.get("dependent_job_type", "controlm"),
        dry_run=dry_run,
    )
    template = infer_template(req["layer"], req["tier"])
    if ok:
        apply_template(table_fqn=req["table_fqn"], template_name=template, dry_run=dry_run)
    return {"success": ok, "template": template}


_UPDATABLE_FIELDS = {
    "domain", "layer", "tier", "owner_email", "ci_number", "stream_id",
    "hk_enabled", "archive_enabled", "lifecycle_enabled", "processing_cadence",
    "controlm_pipeline_job", "controlm_hk_job", "dependent_on_controlm_job",
    "dependent_job_type", "controlm_job_start_time", "controlm_expected_duration_min",
}


def update_table(fqn: str, fields: dict, dry_run: bool) -> bool:
    sets = []
    for key, value in fields.items():
        if key not in _UPDATABLE_FIELDS or value is None:
            continue
        if isinstance(value, bool):
            sets.append(f"{key} = {1 if value else 0}")
        elif isinstance(value, int):
            sets.append(f"{key} = {value}")
        else:
            sets.append(f"{key} = '{_esc(str(value))}'")
    if not sets:
        return False
    sets.append(f"updated_at = '{_now()}'")
    sql = f"UPDATE {STREAM_REGISTRY_TABLE} SET {', '.join(sets)} WHERE table_fqn = '{_esc(fqn)}'"
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


# ── bulk Control-M ────────────────────────────────────────────────────────────

def _build_bulk_where(filters: dict) -> str:
    """
    filters also accepts `exclude_fqns` (list[str]) -- the Manual Bulk Apply
    tab's per-row exclude multiselect (2_Table_Registration.py bc_tab_manual):
    the preview grid lets the user deselect specific matched tables before
    applying, so the apply call carries the same match filters plus an
    explicit exclusion list rather than a second round-trip.
    """
    conds = []
    domain = filters.get("domain")
    layer = filters.get("layer")
    database_name = filters.get("database_name")
    pattern = filters.get("pattern")
    exclude_fqns = filters.get("exclude_fqns")
    if domain:
        conds.append(f"domain = '{_esc(domain)}'")
    if layer:
        conds.append(f"layer = '{_esc(layer)}'")
    if database_name:
        conds.append(f"database_name = '{_esc(database_name)}'")
    if pattern:
        conds.append(f"table_fqn LIKE '%.{_esc(pattern)}%'")
    if exclude_fqns:
        excluded = ", ".join(f"'{_esc(fqn)}'" for fqn in exclude_fqns)
        conds.append(f"table_fqn NOT IN ({excluded})")
    return ("WHERE " + " AND ".join(conds)) if conds else ""


_BULK_SETTABLE = {
    "controlm_pipeline_job", "controlm_hk_job", "dependent_on_controlm_job",
    "dependent_job_type", "controlm_job_start_time", "controlm_expected_duration_min",
    "ci_number", "stream_id",
    # Engine Flags "Bulk Apply" sub-tab reuses this same filter+set mechanism
    # rather than a second bulk endpoint -- domain/layer/database_name filters
    # are identical, only the set_fields differ.
    "hk_enabled", "archive_enabled", "lifecycle_enabled",
}


def bulk_controlm(filters: dict, set_fields: dict, dry_run: bool) -> int:
    where = _build_bulk_where(filters)
    count_df = read_sql(f"SELECT COUNT(*) AS cnt FROM {STREAM_REGISTRY_TABLE} {where}", workgroup="app")
    count = int(count_df.iloc[0]["cnt"]) if not count_df.empty else 0
    if count == 0:
        return 0

    sets = []
    for key, value in set_fields.items():
        if key not in _BULK_SETTABLE or value is None:
            continue
        if isinstance(value, bool):
            sets.append(f"{key} = {1 if value else 0}")
        elif isinstance(value, int):
            sets.append(f"{key} = {value}")
        else:
            sets.append(f"{key} = '{_esc(str(value))}'")
    if not sets:
        return 0
    sets.append(f"updated_at = '{_now()}'")
    sql = f"UPDATE {STREAM_REGISTRY_TABLE} SET {', '.join(sets)} {where}"
    run_query(sql, workgroup="app", dry_run=dry_run)
    return count


# ── job-mapping CSV import / export ──────────────────────────────────────────

_JOB_MAPPING_RENAME = {
    "pipeline_job": "controlm_job_name",
    "hk_job": "hk_controlm_job",
    "gate1_upstream_job": "aws_gate1_job",
}
_JOB_MAPPING_DEFAULTS = [
    ("layer", ""), ("database_name", ""), ("table_pattern", ""),
    ("job_type", "controlm"), ("hk_controlm_job", ""),
    ("aws_gate1_job", ""), ("job_start_time", "02:00"),
    ("expected_duration_min", 0),
]


def import_job_mapping(csv_bytes: bytes, dry_run: bool) -> list[dict]:
    """
    Lifts the Bulk Control-M "Import Job Mapping" tab's parsing + apply
    logic (2_Table_Registration.py) -- blank-row stripping, header rename
    compat, LIKE match preview/apply. Returns the per-row match report.
    """
    df = pd.read_csv(io.BytesIO(csv_bytes), skip_blank_lines=True)
    df.dropna(how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df.columns = [c.strip().lower() for c in df.columns]
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})
    if "domain" in df.columns:
        df = df[df["domain"].ne("")].reset_index(drop=True)

    missing = [c for c in ["domain", "controlm_job_name"] if c not in df.columns]
    df.rename(columns=_JOB_MAPPING_RENAME, inplace=True, errors="ignore")
    missing = [c for c in missing if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}. Required: domain, controlm_job_name.")

    for col, default in _JOB_MAPPING_DEFAULTS:
        if col not in df.columns:
            df[col] = default
    df["expected_duration_min"] = pd.to_numeric(df["expected_duration_min"], errors="coerce").fillna(0).astype(int)

    now = _now()
    report = []
    for _, row in df.iterrows():
        filters = {
            "domain": str(row.get("domain", "")).strip() or None,
            "layer": str(row.get("layer", "")).strip() or None,
            "database_name": str(row.get("database_name", "")).strip() or None,
            "pattern": str(row.get("table_pattern", "")).strip() or None,
        }
        where = _build_bulk_where(filters)
        matched = 0
        if where:
            count_df = read_sql(f"SELECT COUNT(*) AS n FROM {STREAM_REGISTRY_TABLE} {where}", workgroup="app")
            matched = int(count_df.iloc[0]["n"]) if not count_df.empty else 0
            pipeline_job = str(row.get("controlm_job_name", "")).strip()
            gate1_job = str(row.get("aws_gate1_job", "")).strip() or pipeline_job
            hk_job = str(row.get("hk_controlm_job", "")).strip()
            job_type = str(row.get("job_type", "controlm")).strip()
            job_start = str(row.get("job_start_time", "02:00")).strip()
            job_dur = int(row.get("expected_duration_min", 0) or 0)
            if matched and not dry_run:
                run_query(
                    f"UPDATE {STREAM_REGISTRY_TABLE} "
                    f"SET controlm_pipeline_job='{_esc(pipeline_job)}', "
                    f"controlm_hk_job='{_esc(hk_job)}', "
                    f"dependent_on_controlm_job='{_esc(gate1_job)}', "
                    f"dependent_job_type='{_esc(job_type)}', "
                    f"controlm_job_start_time='{_esc(job_start)}', "
                    f"controlm_expected_duration_min={job_dur}, "
                    f"updated_at='{now}' {where}",
                    workgroup="app", dry_run=dry_run,
                )
        report.append({
            "job": str(row.get("controlm_job_name", "")),
            "job_type": str(row.get("job_type", "controlm")),
            "domain": str(row.get("domain", "")),
            "layer": str(row.get("layer", "")),
            "database_name": str(row.get("database_name", "")),
            "table_pattern": str(row.get("table_pattern", "")),
            "tables_matched": matched,
        })
    return report


def export_job_mapping() -> str:
    """CSV string of distinct domain/layer/database rows (export tab's SELECT)."""
    df = read_sql(
        f"""
        SELECT DISTINCT
            domain, layer, database_name,
            '' AS table_pattern,
            COALESCE(controlm_pipeline_job, '')        AS controlm_job_name,
            COALESCE(dependent_job_type, 'controlm')   AS job_type,
            COALESCE(controlm_hk_job, '')               AS hk_controlm_job,
            COALESCE(dependent_on_controlm_job, '')    AS aws_gate1_job,
            COALESCE(controlm_job_start_time, '02:00') AS job_start_time,
            COALESCE(controlm_expected_duration_min, 0)  AS expected_duration_min
        FROM {STREAM_REGISTRY_TABLE}
        WHERE table_format = 'iceberg'
        ORDER BY domain, layer, database_name
        """,
        workgroup="app",
    )
    return df.to_csv(index=False)


# ── Glue catalog browse ───────────────────────────────────────────────────────

def list_glue_databases() -> list[str]:
    return sorted(get_databases())


def list_glue_tables(database: str, pattern: str | None = None, unregistered_only: bool = False) -> list[dict]:
    tables = get_tables(database)
    reg_df = read_sql(
        f"SELECT table_fqn FROM {STREAM_REGISTRY_TABLE} WHERE table_fqn LIKE '%.{_esc(database)}.%'",
        workgroup="app",
    )
    registered = set(reg_df["table_fqn"].tolist()) if not reg_df.empty else set()

    result = []
    for t in tables:
        name = t.get("Name", "")
        fqn = f"glue_catalog.{database}.{name}"
        if pattern:
            raw = pattern.strip()
            needle = raw.replace("%", "").replace("_", "")
            if needle and needle.lower() not in name.lower():
                continue
        is_registered = fqn in registered
        if unregistered_only and is_registered:
            continue
        result.append({
            "name": name,
            "table_fqn": fqn,
            "format": "iceberg" if is_iceberg_table(t) else "hive",
            "registered": is_registered,
        })
    return result
