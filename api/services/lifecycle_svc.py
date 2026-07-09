"""
Zamboni API -- lifecycle service (contracts.md §6 routers/lifecycle.py).

Lifts the state-overview/bulk-exemption-claim/deletion-history query and
mutation patterns from app/pages/9_NonProd_Lifecycle.py.
"""
from __future__ import annotations

from datetime import UTC, datetime

from config.settings import NONPROD_REGISTRY_TABLE
from engine.core.control_plane import read_sql, run_query


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def list_nonprod(env: str, state: str | None, page: int, size: int) -> tuple[list[dict], int]:
    conditions = [f"environment = '{_esc(env)}'", "lifecycle_state != 'DROPPED'"]
    if state:
        conditions.append(f"lifecycle_state = '{_esc(state)}'")
    where = "WHERE " + " AND ".join(conditions)

    total_df = read_sql(f"SELECT COUNT(*) AS cnt FROM {NONPROD_REGISTRY_TABLE} {where}", workgroup="app")
    total = int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0

    offset = max(page - 1, 0) * size
    sql = f"""
        SELECT * FROM {NONPROD_REGISTRY_TABLE} {where}
        ORDER BY lifecycle_state, days_since_activity DESC
        LIMIT {int(size)} OFFSET {int(offset)}
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records"), total


def exempt(fqns: list[str], reason: str, actor: str, dry_run: bool) -> int:
    # nonprod_registry is SQLite-primary now -- this write and the Lifecycle
    # Engine's next scheduled read both hit the same control-plane file, so
    # there's no lag window an exemption could lose a race against (the
    # earlier Athena-primary-plus-cache design needed a synchronous bypass
    # here specifically to avoid that race; this design doesn't).
    now = _now()
    count = 0
    for fqn in fqns:
        run_query(
            f"UPDATE {NONPROD_REGISTRY_TABLE} "
            f"SET lifecycle_state = 'ACTIVE', owner_exempted = 1, "
            f"exemption_reason = '{_esc(reason)}', state_changed_at = '{now}' "
            f"WHERE table_fqn = '{_esc(fqn)}'",
            workgroup="app", dry_run=dry_run,
        )
        count += 1
    return count


def claim(fqns: list[str], reason: str, actor: str, dry_run: bool) -> int:
    now = _now()
    count = 0
    for fqn in fqns:
        run_query(
            f"UPDATE {NONPROD_REGISTRY_TABLE} "
            f"SET lifecycle_state = 'ACTIVE', owner_email = '{_esc(actor)}', "
            f"exemption_reason = '{_esc(reason)}', state_changed_at = '{now}' "
            f"WHERE table_fqn = '{_esc(fqn)}'",
            workgroup="app", dry_run=dry_run,
        )
        count += 1
    return count


def get_config() -> dict:
    from engine.engines.lifecycle_engine import (
        DEFAULT_GREENZONE_DAYS,
        DEFAULT_PENDING_DROP_DAYS,
        DEFAULT_STALE_DAYS,
    )
    return {
        "stale_days": DEFAULT_STALE_DAYS,
        "greenzone_days": DEFAULT_GREENZONE_DAYS,
        "pending_drop_days": DEFAULT_PENDING_DROP_DAYS,
    }


def list_deletions(env: str, page: int, size: int) -> tuple[list[dict], int]:
    where = f"WHERE environment = '{_esc(env)}' AND lifecycle_state = 'DROPPED'"
    total_df = read_sql(f"SELECT COUNT(*) AS cnt FROM {NONPROD_REGISTRY_TABLE} {where}", workgroup="app")
    total = int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0

    offset = max(page - 1, 0) * size
    sql = f"""
        SELECT table_fqn, domain, dropped_at, bytes_reclaimed, s3_cleaned,
               catalog_dropped, previous_state
        FROM {NONPROD_REGISTRY_TABLE} {where}
        ORDER BY dropped_at DESC
        LIMIT {int(size)} OFFSET {int(offset)}
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records"), total
