"""
Zamboni API -- tables service (contracts.md §6 routers/tables.py).

Table-registry query/mutation patterns (browse/register/bulk Control-M/
job-mapping CSV import-export) as a reusable engine-layer function set the
FastAPI router calls.
"""
from __future__ import annotations

import io
from datetime import UTC, datetime

import pandas as pd

from api.services import controlm_svc
from config.settings import STREAM_REGISTRY_TABLE
from engine.core import registry
from engine.core.config import apply_template, infer_template
from engine.core.control_plane import read_sql, run_query, update_row
from engine.utils.glue_client import get_databases, get_tables, guess_partition_column, is_iceberg_table
from engine.utils.partition_utils import parse_table_fqn


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
    # Deliberately NOT registry.get_table() -- see domains_svc.py::get_domain()'s
    # identical comment. Inlined so GET /api/tables/{fqn} shares the same read
    # cache list_tables() (above) already uses.
    df = read_sql(f"SELECT * FROM {STREAM_REGISTRY_TABLE} WHERE table_fqn = '{_esc(fqn)}' LIMIT 1", workgroup="app")
    if df.empty:
        return None
    return df.iloc[0].to_dict()


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
        owner_email=req.get("owner_email", ""),
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
        apply_template(
            table_fqn=req["table_fqn"], template_name=template,
            partition_column=_guess_partition_column_for_registration(req),
            dry_run=dry_run,
        )
    return {"success": ok, "template": template}


def _guess_partition_column_for_registration(req: dict) -> str:
    """
    Best-effort real partition-column guess from the table's actual Glue
    schema, instead of apply_template()'s hardcoded "partition_date" default
    -- register_table() previously never passed partition_column at all, so
    every table registered through Browse & Register silently got
    "partition_date" regardless of what it's really partitioned by.

    Reuses get_tables(database) -- a cache hit, not a new Glue call, since
    the table almost always just came from the cached Browse & Register list
    the caller was looking at. Falls back to "partition_date" (today's
    behavior) for non-Iceberg tables, a cache-miss lookup failure, or a
    schema with no plausibly-named date column -- never raises.
    """
    if req.get("table_format", "iceberg") != "iceberg":
        return "partition_date"
    try:
        _, database, name = parse_table_fqn(req["table_fqn"])
        for t in get_tables(database):
            if t.get("Name") == name:
                columns = t.get("StorageDescriptor", {}).get("Columns", [])
                guess = guess_partition_column(columns)
                return guess["partition_column"] if guess else "partition_date"
    except Exception:
        pass
    return "partition_date"


_UPDATABLE_FIELDS = {
    "domain", "layer", "tier", "owner_email", "ci_number",
    "hk_enabled", "archive_enabled", "lifecycle_enabled", "processing_cadence",
    "controlm_pipeline_job", "controlm_hk_job", "dependent_on_controlm_job",
    "dependent_job_type", "controlm_job_start_time", "controlm_expected_duration_min",
}


def update_table(fqn: str, fields: dict, dry_run: bool) -> bool:
    column_values = {
        key: value for key, value in fields.items()
        if key in _UPDATABLE_FIELDS and value is not None
    }
    if not column_values:
        return False
    update_row(STREAM_REGISTRY_TABLE, "table_fqn", fqn, column_values, dry_run=dry_run)
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
    "ci_number",
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

    if not dry_run:
        job_type = str(set_fields.get("dependent_job_type", "controlm"))
        domain = str(filters.get("domain") or "")
        job_frequency = str(set_fields.get("job_frequency", ""))
        for job_key in ("controlm_pipeline_job", "controlm_hk_job"):
            job_name = set_fields.get(job_key)
            if job_name:
                controlm_svc.register_job_if_missing(str(job_name), job_type, domain, job_frequency)

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
    # A column left entirely blank in the CSV (e.g. table_pattern) is read by
    # pandas as all-NaN float64, not object dtype -- the object-dtype-only
    # loop below would skip it, leaving the raw NaN to later stringify as the
    # literal text "nan" and get used as a LIKE pattern that matches nothing.
    df = df.fillna("")
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
                domain = str(filters.get("domain") or "")
                if pipeline_job:
                    controlm_svc.register_job_if_missing(pipeline_job, job_type, domain)
                if hk_job:
                    controlm_svc.register_job_if_missing(hk_job, job_type, domain)
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
            COALESCE(controlm_hk_job, '')               AS hk_controlm_job,
            COALESCE(dependent_on_controlm_job, '')    AS aws_gate1_job,
            COALESCE(dependent_job_type, 'controlm')   AS job_type,
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
#
# list_glue_databases()/list_glue_tables() read through glue_client's
# GLUE_CATALOG_CACHE_TTL_HOURS cache (default 24h, force_refresh=False) --
# this is the interactive Browse & Register path the cache exists to
# protect, unlike the CLI/scheduled callers elsewhere in the codebase that
# always pass force_refresh=True. rescan_glue_databases()/rescan_glue_tables()
# are the explicit, user-triggered bypass (POST /api/glue/rescan/...).

def list_glue_databases() -> list[str]:
    return sorted(get_databases())


def rescan_glue_databases() -> list[str]:
    return sorted(get_databases(force_refresh=True))


def _build_table_rows(
    tables: list[dict], database: str, pattern: str | None, unregistered_only: bool,
) -> list[dict]:
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

        is_iceberg = is_iceberg_table(t)
        storage    = t.get("StorageDescriptor", {}) or {}
        # Free fields -- already in the cached Glue response, no extra call.
        guess = guess_partition_column(storage.get("Columns", [])) if is_iceberg else None
        create_time = t.get("CreateTime")

        result.append({
            "name": name,
            "table_fqn": fqn,
            "format": "iceberg" if is_iceberg else "hive",
            "registered": is_registered,
            "location": storage.get("Location"),
            "guessed_partition_column": guess["partition_column"] if guess else None,
            "create_time": str(create_time) if create_time else None,
        })
    return result


def list_glue_tables(database: str, pattern: str | None = None, unregistered_only: bool = False) -> list[dict]:
    tables = get_tables(database)
    return _build_table_rows(tables, database, pattern, unregistered_only)


def rescan_glue_tables(database: str, pattern: str | None = None, unregistered_only: bool = False) -> list[dict]:
    tables = get_tables(database, force_refresh=True)
    return _build_table_rows(tables, database, pattern, unregistered_only)
