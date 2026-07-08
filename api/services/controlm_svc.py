"""
Zamboni API -- controlm service (contracts.md §6 routers/controlm.py).

Lifts the Control-M Job Registry tab's query/upsert/bulk-upload logic from
app/pages/2_Table_Registration.py (bc_tab_jobs sub-tab).
"""
from __future__ import annotations

import io
from datetime import UTC, datetime

import pandas as pd

from config.settings import STREAM_REGISTRY_TABLE
from engine.utils.athena_client import read_sql, run_query

_CTRLM_JOBS_TABLE = "controlm_jobs"


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def list_jobs(search: str | None = None) -> list[dict]:
    where = f"WHERE j.job_name LIKE '%{_esc(search)}%'" if search else ""
    sql = f"""
        SELECT j.job_name, j.job_type, j.domain, j.description,
               j.expected_start_time, j.expected_duration_min, j.job_frequency, j.active,
               (SELECT COUNT(*) FROM {STREAM_REGISTRY_TABLE} s
                WHERE s.controlm_pipeline_job = j.job_name
                   OR s.controlm_hk_job = j.job_name
                   OR s.dependent_on_controlm_job = j.job_name) AS tables_mapped
        FROM {_CTRLM_JOBS_TABLE} j {where}
        ORDER BY j.job_name
    """
    return read_sql(sql, workgroup="app").to_dict(orient="records")


def upsert_job(req: dict, registered_by: str) -> bool:
    now = _now()
    run_query(
        f"INSERT OR REPLACE INTO {_CTRLM_JOBS_TABLE} "
        f"(job_name, job_type, domain, description, expected_start_time, "
        f"expected_duration_min, job_frequency, active, registered_by, created_at, updated_at) "
        f"VALUES ('{_esc(req['job_name'])}', '{_esc(req.get('job_type', 'controlm'))}', "
        f"'{_esc(req.get('domain', ''))}', '{_esc(req.get('description', ''))}', "
        f"'{_esc(req.get('expected_start_time', ''))}', {int(req.get('expected_duration_min', 0))}, "
        f"'{_esc(req.get('job_frequency', ''))}', "
        f"1, '{_esc(registered_by)}', '{now}', '{now}')",
        workgroup="app", dry_run=False,
    )
    return True


def register_job_if_missing(
    job_name: str, job_type: str = "controlm", domain: str = "", job_frequency: str = "",
) -> None:
    """
    Ensure a job name referenced by Manual Bulk Apply or Import Job Mapping
    shows up in the Control-M Job Registry -- those flows write job names
    onto stream_registry rows directly and never touched controlm_jobs,
    which is why jobs applied there never appeared in the registry grid.
    INSERT OR IGNORE (not upsert_job's INSERT OR REPLACE): if the job is
    already registered, its curated description/start-time/frequency is
    left untouched -- a bulk-apply side effect should never clobber data
    someone entered by hand in the Job Registry itself.
    """
    job_name = job_name.strip()
    if not job_name:
        return
    now = _now()
    run_query(
        f"INSERT OR IGNORE INTO {_CTRLM_JOBS_TABLE} "
        f"(job_name, job_type, domain, description, expected_start_time, "
        f"expected_duration_min, job_frequency, active, registered_by, created_at, updated_at) "
        f"VALUES ('{_esc(job_name)}', '{_esc(job_type)}', '{_esc(domain)}', '', "
        f"'', 0, '{_esc(job_frequency)}', 1, 'auto:bulk_apply', '{now}', '{now}')",
        workgroup="app", dry_run=False,
    )


def import_jobs(csv_bytes: bytes, registered_by: str) -> dict:
    df = pd.read_csv(io.BytesIO(csv_bytes), skip_blank_lines=True)
    df.dropna(how="all", inplace=True)
    df.columns = [c.strip().lower() for c in df.columns]
    # See the identical fillna("") note in tables_svc.py::import_job_mapping --
    # an all-blank optional column reads as all-NaN float64 and would
    # otherwise bypass the object-dtype-only string cleanup below.
    df = df.fillna("")
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})
    if "job_name" not in df.columns:
        raise ValueError("CSV must have a `job_name` column.")
    df = df[df["job_name"].ne("")]

    for col, default in [
        ("job_type", "controlm"), ("domain", ""), ("description", ""),
        ("expected_start_time", ""), ("expected_duration_min", 0), ("job_frequency", ""),
    ]:
        if col not in df.columns:
            df[col] = default
    df["expected_duration_min"] = pd.to_numeric(df["expected_duration_min"], errors="coerce").fillna(0).astype(int)

    ok = fail = 0
    for _, row in df.iterrows():
        try:
            upsert_job(row.to_dict(), registered_by)
            ok += 1
        except Exception:
            fail += 1
    return {"imported": ok, "failed": fail}


def delete_job(job_name: str) -> bool:
    run_query(f"DELETE FROM {_CTRLM_JOBS_TABLE} WHERE job_name = '{_esc(job_name)}'", workgroup="app", dry_run=False)
    return True


def get_mapped_tables(job_name: str) -> list[dict]:
    """Tables referencing job_name in any of the three Control-M role
    columns -- backs the Job List "Tables Mapped" count's drill-in popup."""
    esc = _esc(job_name)
    sql = f"""
        SELECT table_fqn, domain, layer, tier,
               controlm_pipeline_job, controlm_hk_job, dependent_on_controlm_job
        FROM {STREAM_REGISTRY_TABLE}
        WHERE controlm_pipeline_job = '{esc}'
           OR controlm_hk_job = '{esc}'
           OR dependent_on_controlm_job = '{esc}'
        ORDER BY table_fqn
    """
    return read_sql(sql, workgroup="app").to_dict(orient="records")
