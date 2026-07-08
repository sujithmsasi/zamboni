"""
Zamboni API -- controlm service (contracts.md §6 routers/controlm.py).

Lifts the Control-M Job Registry tab's query/upsert/bulk-upload logic from
app/pages/2_Table_Registration.py (bc_tab_jobs sub-tab).
"""
from __future__ import annotations

import io
from datetime import UTC, datetime

import pandas as pd

from engine.utils.athena_client import read_sql, run_query

_CTRLM_JOBS_TABLE = "controlm_jobs"


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def list_jobs(search: str | None = None) -> list[dict]:
    where = f"WHERE job_name LIKE '%{_esc(search)}%'" if search else ""
    sql = f"""
        SELECT job_name, job_type, domain, description,
               expected_start_time, expected_duration_min, active
        FROM {_CTRLM_JOBS_TABLE} {where}
        ORDER BY job_name
    """
    return read_sql(sql, workgroup="app").to_dict(orient="records")


def upsert_job(req: dict, registered_by: str) -> bool:
    now = _now()
    run_query(
        f"INSERT OR REPLACE INTO {_CTRLM_JOBS_TABLE} "
        f"(job_name, job_type, domain, description, expected_start_time, "
        f"expected_duration_min, active, registered_by, created_at, updated_at) "
        f"VALUES ('{_esc(req['job_name'])}', '{_esc(req.get('job_type', 'controlm'))}', "
        f"'{_esc(req.get('domain', ''))}', '{_esc(req.get('description', ''))}', "
        f"'{_esc(req.get('expected_start_time', ''))}', {int(req.get('expected_duration_min', 0))}, "
        f"1, '{_esc(registered_by)}', '{now}', '{now}')",
        workgroup="app", dry_run=False,
    )
    return True


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
        ("expected_start_time", ""), ("expected_duration_min", 0),
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
