"""
Zamboni — Lock Service (Gate 0 / Workstream A Safety Core, contracts.md §3.1)

Coordinates exclusive per-table maintenance access so compaction, snapshot
expiry, and orphan cleanup never race against each other or against AWS
Glue's own table optimizer -- the root cause of the metadata-loss incident
behind Workstream A.

Backend selection is driven by config.settings.get_mode():
  local              -> SQLite table `maintenance_locks` (via engine.utils.local_db)
  aws_local/aws_ec2  -> DynamoDB table config.settings.DDB_LOCK_TABLE

DynamoDB semantics (contracts §3.1, exact):
  acquire   -- PutItem,    ConditionExpression: attribute_not_exists(table_fqn) OR expires_at < :now
  heartbeat -- UpdateItem, ConditionExpression: lock_owner = :me
  release   -- DeleteItem, ConditionExpression: lock_owner = :me

SQLite semantics (contracts §3.1): single transaction -- DELETE expired rows
for this table_fqn, then INSERT; a PK conflict (still-held lock) means the
table is held by another owner.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from config.settings import (
    AWS_REGION,
    DDB_LOCK_TABLE,
    LOCK_HEARTBEAT_SECONDS,
    LOCK_TTL_MINUTES,
    get_boto3_session,
    get_mode,
)
from engine.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Lock:
    """A held maintenance lock. Callers keep this around to heartbeat/release."""
    table_fqn:    str
    lock_owner:   str
    operation:    str
    acquired_at:  datetime
    heartbeat_at: datetime
    expires_at:   datetime


def _make_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class LockLostError(RuntimeError):
    """
    Raised by LockHeartbeat.assert_held() once the lease has been
    confirmed lost -- either heartbeat() was explicitly rejected (someone
    else's lock_owner is now on record, meaning the lease genuinely
    expired and was stolen) or it failed too many consecutive times to
    still trust that this process is the real owner. Callers doing
    destructive work (a VACUUM call, a Glue DROP, an S3 sweep) must call
    assert_held() immediately before the irreversible step so a lease lost
    mid-run stops the operation instead of completing it unprotected.
    """


class LockHeartbeat:
    """
    Background ticker + lease-ownership tracker for a held Lock, usable
    as a context manager (`with LockHeartbeat(lock_service, lock) as hb:`)
    for the duration of a long-running per-table operation (orchestrated
    HK maintenance, archival, lifecycle cleanup).

    2026-07-10 audit fix: LOCK_HEARTBEAT_SECONDS and LockService.heartbeat()
    both existed (contracts.md §3.1) but nothing ever called heartbeat() --
    a genuinely long operation (a bloated table's compaction Glue job can
    run up to GLUE_JOB_TIMEOUT_SECONDS, close to LOCK_TTL_MINUTES) could
    outlive its lock's expiry and let a second trigger acquire the
    "released" lock while the first run is still in flight -- exactly the
    concurrent-maintenance race locking exists to prevent. A background
    thread renews on a fixed wall-clock cadence for as long as the caller
    holds it, covering the whole run regardless of which internal call
    (Glue poll, Athena poll, S3 sweep, or anything added later) happens to
    be slow.

    2026-07-11 audit fix: the first version only logged a heartbeat
    failure and kept going -- ownership is a LEASE, not a fire-and-forget
    background log line, and nothing ever observed a lost one. Now:
      - a REJECTED heartbeat (lock_service.heartbeat() returns False --
        someone else's lock_owner is on record) marks the lease lost
        immediately, since that means it was genuinely stolen;
      - MAX_CONSECUTIVE_FAILURES heartbeat *errors* in a row (DynamoDB/
        SQLite unreachable, not a rejection) also marks it lost -- past
        that point we can no longer trust the lease is still being
        renewed, even without an explicit theft signal.
    Once lost, `lost` is permanently True and assert_held() raises
    LockLostError on every subsequent call. Callers pass a
    `cancel_check=lambda: heartbeat.lost` into long-running polling loops
    (engine.utils.athena_client._poll, engine.operations.compaction.
    _wait_for_glue_job) so a lease lost mid-wait actively cancels the
    in-flight query/job instead of completing it unprotected, and call
    assert_held() immediately before every irreversible step (a VACUUM
    call, a Glue DROP, a partition DELETE, an S3 sweep) so no new
    destructive operation ever starts once the lease is gone.
    """

    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, lock_service: LockService, lock: Lock, interval_s: int = LOCK_HEARTBEAT_SECONDS):
        self._lock_service = lock_service
        self._lock         = lock
        self._interval_s   = interval_s
        self._stop_event   = threading.Event()
        self._lost_event   = threading.Event()
        self._consecutive_failures = 0
        self._thread       = threading.Thread(target=self._run, daemon=True)

    @property
    def lost(self) -> bool:
        """True once the lease has been confirmed lost (rejected heartbeat
        or too many consecutive failures). Permanent -- never resets."""
        return self._lost_event.is_set()

    def assert_held(self) -> None:
        """Raise LockLostError if the lease has been lost. Call this
        immediately before every irreversible/destructive step."""
        if self._lost_event.is_set():
            raise LockLostError(
                f"lease for {self._lock.table_fqn} (owner={self._lock.lock_owner}) was lost"
            )

    def start(self) -> LockHeartbeat:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=self._interval_s + 5)

    def __enter__(self) -> LockHeartbeat:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            try:
                ok = self._lock_service.heartbeat(self._lock)
            except Exception as e:
                log.warning(
                    "lock_heartbeat.tick_failed",
                    table_fqn=self._lock.table_fqn, error=str(e),
                )
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self._mark_lost(
                        f"heartbeat failed {self._consecutive_failures} consecutive times: {e}"
                    )
                continue

            if not ok:
                self._mark_lost("heartbeat rejected -- lease no longer owned by this process")
                continue

            self._consecutive_failures = 0

    def _mark_lost(self, reason: str) -> None:
        if not self._lost_event.is_set():
            log.error("lock_heartbeat.lease_lost", table_fqn=self._lock.table_fqn, reason=reason)
        self._lost_event.set()


class LockService:
    """
    Acquire / heartbeat / release per-table maintenance locks.

    Args:
        mode: Override the backend ("local" | "aws_local" | "aws_ec2").
              Defaults to config.settings.get_mode() -- exposed as a
              constructor param so tests can pin a backend without mutating
              process-wide environment variables.
    """

    def __init__(self, mode: str | None = None):
        self.mode = mode or get_mode()
        self._ddb_client = None

    # ── Public API ────────────────────────────────────────────────────────────

    def acquire(
        self,
        table_fqn: str,
        operation: str,
        ttl_min: int = LOCK_TTL_MINUTES,
    ) -> Lock | None:
        """Attempt to acquire the lock for table_fqn. None if already held."""
        owner   = _make_owner()
        now     = datetime.now(UTC)
        expires = now + timedelta(minutes=ttl_min)

        if self.mode == "local":
            ok = self._sqlite_acquire(table_fqn, owner, operation, now, expires)
        else:
            ok = self._ddb_acquire(table_fqn, owner, operation, now, expires)

        if not ok:
            log.info("lock_service.contend", table_fqn=table_fqn, operation=operation)
            return None

        log.info(
            "lock_service.acquired",
            table_fqn=table_fqn, operation=operation, lock_owner=owner, mode=self.mode,
        )
        return Lock(
            table_fqn=table_fqn, lock_owner=owner, operation=operation,
            acquired_at=now, heartbeat_at=now, expires_at=expires,
        )

    def heartbeat(self, lock: Lock, ttl_min: int = LOCK_TTL_MINUTES) -> bool:
        """Extend a held lock's expiry. False if no longer owned by this lock."""
        now     = datetime.now(UTC)
        expires = now + timedelta(minutes=ttl_min)

        if self.mode == "local":
            ok = self._sqlite_heartbeat(lock, now, expires)
        else:
            ok = self._ddb_heartbeat(lock, now, expires)

        if ok:
            lock.heartbeat_at = now
            lock.expires_at   = expires
            log.debug("lock_service.heartbeat", table_fqn=lock.table_fqn, lock_owner=lock.lock_owner)
        else:
            log.warning(
                "lock_service.heartbeat_rejected",
                table_fqn=lock.table_fqn, lock_owner=lock.lock_owner,
            )
        return ok

    def release(self, lock: Lock) -> None:
        """Release a held lock. No-op (logged) if no longer owned by this lock."""
        if self.mode == "local":
            ok = self._sqlite_release(lock)
        else:
            ok = self._ddb_release(lock)

        if ok:
            log.info("lock_service.released", table_fqn=lock.table_fqn, lock_owner=lock.lock_owner)
        else:
            log.warning(
                "lock_service.release_rejected",
                table_fqn=lock.table_fqn, lock_owner=lock.lock_owner,
            )

    def list_locks(self) -> list[dict]:
        """Return all currently-held locks (contracts.md §6 GET /api/locks)."""
        if self.mode == "local":
            return self._sqlite_list()
        return self._ddb_list()

    def release_force(self, table_fqn: str) -> bool:
        """
        Admin force-release, ignoring lock_owner (contracts.md §6
        DELETE /api/locks/{fqn}) -- callers are responsible for auditing.
        Returns True if a lock was actually removed.
        """
        if self.mode == "local":
            return self._sqlite_release_force(table_fqn)
        return self._ddb_release_force(table_fqn)

    # ── SQLite backend: list / force-release ─────────────────────────────────

    def _sqlite_list(self) -> list[dict]:
        from engine.utils.local_db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT table_fqn, lock_owner, operation, acquired_at, "
            "heartbeat_at, expires_at FROM maintenance_locks"
        ).fetchall()
        return [dict(r) for r in rows]

    def _sqlite_release_force(self, table_fqn: str) -> bool:
        from engine.utils.local_db import get_connection
        conn = get_connection()
        cur = conn.execute("DELETE FROM maintenance_locks WHERE table_fqn = ?", (table_fqn,))
        conn.commit()
        return cur.rowcount > 0

    # ── DynamoDB backend: list / force-release ───────────────────────────────

    def _ddb_list(self) -> list[dict]:
        client = self._client()
        items: list[dict] = []
        kwargs: dict = {"TableName": DDB_LOCK_TABLE}
        while True:
            resp = client.scan(**kwargs)
            for i in resp.get("Items", []):
                items.append({
                    "table_fqn":    i["table_fqn"]["S"],
                    "lock_owner":   i["lock_owner"]["S"],
                    "operation":    i["operation"]["S"],
                    "acquired_at":  i["acquired_at"]["S"],
                    "heartbeat_at": i["heartbeat_at"]["S"],
                    "expires_at":   int(i["expires_at"]["N"]),
                })
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def _ddb_release_force(self, table_fqn: str) -> bool:
        client = self._client()
        try:
            client.delete_item(TableName=DDB_LOCK_TABLE, Key={"table_fqn": {"S": table_fqn}})
            return True
        except Exception as e:
            log.warning("lock_service.release_force_failed", table_fqn=table_fqn, error=str(e))
            return False

    # ── SQLite backend (local mode) ──────────────────────────────────────────

    def _sqlite_acquire(
        self, table_fqn: str, owner: str, operation: str, now: datetime, expires: datetime,
    ) -> bool:
        from engine.utils.local_db import get_connection
        conn      = get_connection()
        now_epoch = int(now.timestamp())
        try:
            cur = conn.execute(
                "DELETE FROM maintenance_locks WHERE table_fqn = ? AND expires_at < ?",
                (table_fqn, now_epoch),
            )
            if cur.rowcount > 0:
                log.info("lock_service.stole_expired", table_fqn=table_fqn)
            conn.execute(
                "INSERT INTO maintenance_locks "
                "(table_fqn, lock_owner, operation, acquired_at, heartbeat_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (table_fqn, owner, operation, now.isoformat(), now.isoformat(), int(expires.timestamp())),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            conn.rollback()
            return False

    def _sqlite_heartbeat(self, lock: Lock, now: datetime, expires: datetime) -> bool:
        from engine.utils.local_db import get_connection
        conn = get_connection()
        cur = conn.execute(
            "UPDATE maintenance_locks SET heartbeat_at = ?, expires_at = ? "
            "WHERE table_fqn = ? AND lock_owner = ?",
            (now.isoformat(), int(expires.timestamp()), lock.table_fqn, lock.lock_owner),
        )
        conn.commit()
        return cur.rowcount > 0

    def _sqlite_release(self, lock: Lock) -> bool:
        from engine.utils.local_db import get_connection
        conn = get_connection()
        cur = conn.execute(
            "DELETE FROM maintenance_locks WHERE table_fqn = ? AND lock_owner = ?",
            (lock.table_fqn, lock.lock_owner),
        )
        conn.commit()
        return cur.rowcount > 0

    # ── DynamoDB backend (aws_local / aws_ec2) ───────────────────────────────

    def _client(self):
        if self._ddb_client is None:
            self._ddb_client = get_boto3_session().client("dynamodb", region_name=AWS_REGION)
        return self._ddb_client

    def _ddb_acquire(
        self, table_fqn: str, owner: str, operation: str, now: datetime, expires: datetime,
    ) -> bool:
        client = self._client()
        try:
            client.put_item(
                TableName=DDB_LOCK_TABLE,
                Item={
                    "table_fqn":    {"S": table_fqn},
                    "lock_owner":   {"S": owner},
                    "operation":    {"S": operation},
                    "acquired_at":  {"S": now.isoformat()},
                    "heartbeat_at": {"S": now.isoformat()},
                    "expires_at":   {"N": str(int(expires.timestamp()))},
                },
                ConditionExpression="attribute_not_exists(table_fqn) OR expires_at < :now",
                ExpressionAttributeValues={":now": {"N": str(int(now.timestamp()))}},
            )
            return True
        except client.exceptions.ConditionalCheckFailedException:
            return False

    def _ddb_heartbeat(self, lock: Lock, now: datetime, expires: datetime) -> bool:
        client = self._client()
        try:
            client.update_item(
                TableName=DDB_LOCK_TABLE,
                Key={"table_fqn": {"S": lock.table_fqn}},
                UpdateExpression="SET heartbeat_at = :hb, expires_at = :exp",
                ConditionExpression="lock_owner = :me",
                ExpressionAttributeValues={
                    ":hb":  {"S": now.isoformat()},
                    ":exp": {"N": str(int(expires.timestamp()))},
                    ":me":  {"S": lock.lock_owner},
                },
            )
            return True
        except client.exceptions.ConditionalCheckFailedException:
            return False

    def _ddb_release(self, lock: Lock) -> bool:
        client = self._client()
        try:
            client.delete_item(
                TableName=DDB_LOCK_TABLE,
                Key={"table_fqn": {"S": lock.table_fqn}},
                ConditionExpression="lock_owner = :me",
                ExpressionAttributeValues={":me": {"S": lock.lock_owner}},
            )
            return True
        except client.exceptions.ConditionalCheckFailedException:
            return False
