"""
Zamboni API tests -- conftest.

IMPORTANT: env vars here MUST be set before any `config`/`engine` module is
first imported in this process, since config/settings.py reads them once at
import time (ZAMBONI_LOCAL_MODE, ZAMBONI_LOCAL_DB). Run this suite as its own
`pytest tests/api` invocation (fresh interpreter) rather than combined into
the same process as tests/unit, which mocks AWS calls directly and does not
need local mode wired process-wide.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_TMP_DB = Path(__file__).resolve().parent / "_zamboni_api_test.db"

os.environ["ZAMBONI_TEST_MODE"] = "true"
os.environ["ZAMBONI_LOCAL_MODE"] = "true"
os.environ["ZAMBONI_MODE"] = "local"
os.environ["ZAMBONI_LOCAL_DB"] = str(_TMP_DB)
os.environ["ZAMBONI_USER"] = "test-user"
os.environ["EXECUTION_LOG_MODE"] = "insert"
os.environ.setdefault("PYTHONUTF8", "1")

if sys.flags.utf8_mode == 0:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from scripts.seed_local_db import main as _seed_main  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _seeded_db():
    if _TMP_DB.exists():
        _TMP_DB.unlink()
    _seed_main()

    from engine.utils.local_db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO controlm_jobs "
        "(job_name, job_type, domain, description, expected_start_time, "
        "expected_duration_min, active, registered_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("ACE-DA-FIN-APS-INGEST-PRD", "controlm", "finance", "seed job",
         "02:00", 30, 1, "seed", "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
    )
    conn.commit()
    yield
    conn.close()
    if _TMP_DB.exists():
        try:
            _TMP_DB.unlink()
        except PermissionError:
            pass


@pytest.fixture()
def client() -> TestClient:
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def a_table_fqn() -> str:
    """Return one real table_fqn from the seeded stream_registry."""
    from config.settings import STREAM_REGISTRY_TABLE
    from engine.utils.athena_client import read_sql
    df = read_sql(f"SELECT table_fqn FROM {STREAM_REGISTRY_TABLE} LIMIT 1", workgroup="app")
    return df.iloc[0]["table_fqn"]
