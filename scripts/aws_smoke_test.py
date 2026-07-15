"""
Zamboni — AWS Connectivity Smoke Test (Phase 6, closes the backlog item)

Exercises every AWS service Zamboni touches in aws_local/aws_ec2 mode --
STS identity, Glue catalog read, Athena query, S3 read/write, SNS, the
DynamoDB lock table, one live Glue GetTableOptimizer call, and the
SQLite control-plane DB (engine/core/control_plane.py) -- so a demo
laptop or a freshly-deployed EC2 instance can be checked in one command
before relying on it. In ZAMBONI_MODE=local every check reports SKIPPED
(no AWS calls happen at all); this is the expected, not-broken, result
when running against the local SQLite fallback.

The control-plane check is the one most worth paying attention to on a
fresh EC2 deploy: in aws_ec2 mode it FAILs if ZAMBONI_CONTROL_PLANE_DB
resolves inside /opt/zamboni, since CodeDeploy wipes that directory on
every revision -- catching the exact silent-data-loss trap this check
was added for, not just "is the file there right now."

Usage:
    python scripts/aws_smoke_test.py                       # uses get_mode()
    python scripts/aws_smoke_test.py --create-lock-table    # create the DynamoDB
                                                             # lock table if missing
    python scripts/aws_smoke_test.py --init-control-plane-db # run
                                                             # scripts/init_control_plane_db.py
                                                             # if the control-plane DB/tables are missing
    python scripts/aws_smoke_test.py --json                 # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (  # noqa: E402
    ATHENA_DATABASE,
    ATHENA_RESULTS_BUCKET,
    ATHENA_WORKGROUPS,
    AWS_REGION,
    DDB_LOCK_TABLE,
    SNS_ALERT_TOPIC_ARN,
    STREAM_REGISTRY_TABLE,
    get_boto3_session,
    get_mode,
)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIPPED"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _skip(name: str, reason: str) -> CheckResult:
    return CheckResult(name, SKIP, reason)


def _run_checks(mode: str, create_lock_table: bool, init_control_plane_db: bool) -> list[CheckResult]:
    if mode == "local":
        reason = "ZAMBONI_MODE=local -- no AWS calls are made in local mode"
        return [
            _skip("sts_identity", reason),
            _skip("glue_list_databases", reason),
            _skip("athena_select_1", reason),
            _skip("s3_put_delete", reason),
            _skip("sns_get_topic_attributes", reason),
            _skip("dynamodb_lock_table", reason),
            _skip("glue_get_table_optimizer", reason),
            _skip("control_plane_db", reason),
        ]

    session = get_boto3_session()
    results: list[CheckResult] = []

    results.append(_check_sts(session))
    results.append(_check_glue_databases(session))
    results.append(_check_athena_select_1(session))
    results.append(_check_s3_put_delete(session))
    results.append(_check_sns(session))
    results.append(_check_dynamodb_lock_table(session, create_lock_table))
    results.append(_check_get_table_optimizer(session))
    results.append(_check_control_plane_db(mode, init_control_plane_db))
    return results


def _check_sts(session) -> CheckResult:
    try:
        identity = session.client("sts", region_name=AWS_REGION).get_caller_identity()
        return CheckResult(
            "sts_identity", PASS,
            f"account={identity['Account']} arn={identity['Arn']}",
        )
    except Exception as e:
        return CheckResult("sts_identity", FAIL, str(e))


def _check_glue_databases(session) -> CheckResult:
    try:
        resp = session.client("glue", region_name=AWS_REGION).get_databases(MaxResults=10)
        names = [d["Name"] for d in resp.get("DatabaseList", [])]
        return CheckResult(
            "glue_list_databases", PASS,
            f"{len(names)} database(s) visible, e.g. {names[:3]}",
        )
    except Exception as e:
        return CheckResult("glue_list_databases", FAIL, str(e))


def _check_athena_select_1(session) -> CheckResult:
    import time

    client = session.client("athena", region_name=AWS_REGION)
    workgroup = ATHENA_WORKGROUPS["app"]
    try:
        resp = client.start_query_execution(
            QueryString="SELECT 1",
            WorkGroup=workgroup,
            QueryExecutionContext={"Database": ATHENA_DATABASE},
        )
        query_id = resp["QueryExecutionId"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                return CheckResult("athena_select_1", PASS, f"workgroup={workgroup} query_id={query_id}")
            if state in ("FAILED", "CANCELLED"):
                reason = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"].get(
                    "StateChangeReason", "unknown"
                )
                return CheckResult("athena_select_1", FAIL, f"{state}: {reason}")
            time.sleep(2)
        return CheckResult("athena_select_1", FAIL, f"query {query_id} did not complete within 60s")
    except Exception as e:
        return CheckResult("athena_select_1", FAIL, str(e))


def _check_s3_put_delete(session) -> CheckResult:
    bucket_uri = ATHENA_RESULTS_BUCKET
    assert bucket_uri.startswith("s3://"), f"ATHENA_RESULTS_BUCKET is not an s3:// URI: {bucket_uri}"
    bucket = bucket_uri[len("s3://"):].split("/", 1)[0]
    key = "zamboni/_smoke_test/probe.txt"
    try:
        client = session.client("s3", region_name=AWS_REGION)
        client.put_object(Bucket=bucket, Key=key, Body=b"zamboni-smoke-test")
        client.delete_object(Bucket=bucket, Key=key)
        return CheckResult("s3_put_delete", PASS, f"bucket={bucket} key={key}")
    except Exception as e:
        return CheckResult("s3_put_delete", FAIL, str(e))


def _check_sns(session) -> CheckResult:
    try:
        client = session.client("sns", region_name=AWS_REGION)
        attrs = client.get_topic_attributes(TopicArn=SNS_ALERT_TOPIC_ARN)
        return CheckResult(
            "sns_get_topic_attributes", PASS,
            f"topic={SNS_ALERT_TOPIC_ARN} subscriptions={attrs['Attributes'].get('SubscriptionsConfirmed')}",
        )
    except Exception as e:
        return CheckResult("sns_get_topic_attributes", FAIL, str(e))


def _check_dynamodb_lock_table(session, create_if_missing: bool) -> CheckResult:
    client = session.client("dynamodb", region_name=AWS_REGION)
    try:
        desc = client.describe_table(TableName=DDB_LOCK_TABLE)
        status = desc["Table"]["TableStatus"]
        return CheckResult("dynamodb_lock_table", PASS, f"table={DDB_LOCK_TABLE} status={status}")
    except client.exceptions.ResourceNotFoundException:
        if not create_if_missing:
            return CheckResult(
                "dynamodb_lock_table", FAIL,
                f"table={DDB_LOCK_TABLE} does not exist -- re-run with --create-lock-table",
            )
        try:
            from scripts.create_lock_table import create_lock_table
            create_lock_table()
            return CheckResult("dynamodb_lock_table", PASS, f"table={DDB_LOCK_TABLE} created")
        except Exception as e:
            return CheckResult("dynamodb_lock_table", FAIL, f"create failed: {e}")
    except Exception as e:
        return CheckResult("dynamodb_lock_table", FAIL, str(e))


def _check_get_table_optimizer(session) -> CheckResult:
    from engine.utils.athena_client import read_sql
    from engine.utils.partition_utils import parse_table_fqn

    # stream_registry has no database_name/table_name columns -- only a
    # single table_fqn ('catalog.database.table'), per its own DDL comment
    # in sql/create_stream_registry.sql. A prior version of this check
    # queried the two split-out column names directly, which never existed
    # in the real Athena schema -- confirmed via a real run once the
    # underlying credential issue this check was actually failing on
    # (UnrecognizedClientException) was fixed and this query could finally
    # reach Athena at all.
    try:
        df = read_sql(
            f"SELECT table_fqn FROM {STREAM_REGISTRY_TABLE} LIMIT 1",
            workgroup="app",
        )
    except Exception as e:
        return CheckResult("glue_get_table_optimizer", FAIL, f"could not read {STREAM_REGISTRY_TABLE}: {e}")

    if df.empty:
        return CheckResult("glue_get_table_optimizer", SKIP, f"no rows in {STREAM_REGISTRY_TABLE} to probe")

    try:
        _catalog, database, table = parse_table_fqn(str(df.iloc[0]["table_fqn"]))
    except ValueError as e:
        return CheckResult("glue_get_table_optimizer", FAIL, f"unparseable table_fqn: {e}")
    client = session.client("glue", region_name=AWS_REGION)
    for optimizer_type in ("compaction", "retention", "orphan_file_deletion"):
        try:
            client.get_table_optimizer(DatabaseName=database, TableName=table, Type=optimizer_type)
        except client.exceptions.EntityNotFoundException:
            pass  # optimizer not configured -- API call itself succeeded
        except Exception as e:
            return CheckResult(
                "glue_get_table_optimizer", FAIL,
                f"{database}.{table} type={optimizer_type}: {e}",
            )
    return CheckResult("glue_get_table_optimizer", PASS, f"probed {database}.{table}")


_CONTROL_PLANE_TABLES = {"stream_registry", "hk_config", "domain_registry", "nonprod_registry", "controlm_jobs"}


def _check_control_plane_db(mode: str, init_if_missing: bool) -> CheckResult:
    """
    Verifies the SQLite control-plane DB (engine/core/control_plane.py) is
    reachable and has the 5 expected tables. In aws_ec2 mode, also verifies
    ZAMBONI_CONTROL_PLANE_DB isn't pointing inside /opt/zamboni -- CodeDeploy
    wipes that directory on every revision, so a bare/relative path there
    would silently destroy every registered domain/table/policy on the next
    deploy. See the warning in .env.example for the same trap.
    """
    import os
    import sqlite3

    from config.settings import ZAMBONI_CONTROL_PLANE_DB

    path = ZAMBONI_CONTROL_PLANE_DB
    if mode == "aws_ec2":
        # Deliberately a raw string-prefix check, not os.path.abspath() --
        # this target path is always a Linux path on the real EC2 instance
        # this mode runs on, and abspath() would resolve a leading "/" onto
        # whatever drive this happens to run from if ever invoked on
        # Windows, silently defeating the /opt/zamboni prefix check.
        normalized = path.replace("\\", "/")
        if not normalized.startswith("/") or normalized.startswith("/opt/zamboni"):
            return CheckResult(
                "control_plane_db", FAIL,
                f"ZAMBONI_CONTROL_PLANE_DB='{path}' is not a persistent absolute path outside "
                "/opt/zamboni -- CodeDeploy wipes /opt/zamboni on every revision, which would "
                "silently destroy this database on the next deploy. Set it to "
                "/data/zamboni/zamboni_control.db in the instance's .env.",
            )

    if not os.path.isfile(path):
        if init_if_missing:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                from scripts.init_control_plane_db import main as init_main
                init_main()
            except Exception as e:
                return CheckResult("control_plane_db", FAIL, f"init failed for '{path}': {e}")
        else:
            return CheckResult(
                "control_plane_db", FAIL,
                f"control-plane DB not found at '{path}' -- re-run with --init-control-plane-db",
            )

    try:
        conn = sqlite3.connect(path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()
    except Exception as e:
        return CheckResult("control_plane_db", FAIL, f"could not open '{path}': {e}")

    missing = _CONTROL_PLANE_TABLES - tables
    if missing:
        return CheckResult(
            "control_plane_db", FAIL,
            f"'{path}' is missing table(s) {sorted(missing)} -- re-run with --init-control-plane-db",
        )
    return CheckResult("control_plane_db", PASS, f"path={path}, {len(_CONTROL_PLANE_TABLES)} control-plane table(s) present")


def _print_table(mode: str, results: list[CheckResult]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print(f"\n[bold]Zamboni AWS Smoke Test[/]  (mode=[cyan]{mode}[/])\n")

    t = Table(show_header=True, header_style="bold blue")
    t.add_column("Check", style="white", no_wrap=True)
    t.add_column("Status")
    t.add_column("Detail", overflow="fold")

    colors = {PASS: "green", FAIL: "red", SKIP: "yellow"}
    for r in results:
        color = colors.get(r.status, "white")
        t.add_row(r.name, f"[{color}]{r.status}[/]", r.detail)

    console.print(t)

    n_fail = sum(1 for r in results if r.status == FAIL)
    if n_fail:
        console.print(f"\n[red]{n_fail} check(s) FAILED.[/]")
    else:
        console.print("\n[green]All checks PASSED or SKIPPED cleanly.[/]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Zamboni AWS connectivity smoke test")
    parser.add_argument(
        "--create-lock-table", action="store_true",
        help="Create the DynamoDB lock table via scripts/create_lock_table.py if it's missing",
    )
    parser.add_argument(
        "--init-control-plane-db", action="store_true",
        help="Run scripts/init_control_plane_db.py if the control-plane DB/tables are missing",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a table")
    args = parser.parse_args()

    mode = get_mode()
    results = _run_checks(mode, args.create_lock_table, args.init_control_plane_db)
    all_ok = all(r.status != FAIL for r in results)

    if args.json:
        payload = {
            "mode": mode,
            "all_passed": all_ok,
            "checks": [{"name": r.name, "status": r.status, "detail": r.detail} for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_table(mode, results)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
