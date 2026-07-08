"""
Zamboni — AWS Connectivity Smoke Test (Phase 6, closes the backlog item)

Exercises every AWS service Zamboni touches in aws_local/aws_ec2 mode --
STS identity, Glue catalog read, Athena query, S3 read/write, SNS, the
DynamoDB lock table, and one live Glue GetTableOptimizer call -- so a demo
laptop or a freshly-deployed EC2 instance can be checked in one command
before relying on it. In ZAMBONI_MODE=local every AWS check reports
SKIPPED (no AWS calls happen at all); this is the expected, not-broken,
result when running against the local SQLite fallback.

Usage:
    python scripts/aws_smoke_test.py                    # uses get_mode()
    python scripts/aws_smoke_test.py --create-lock-table # create the DynamoDB
                                                          # lock table if missing
    python scripts/aws_smoke_test.py --json              # machine-readable output
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


def _run_checks(mode: str, create_lock_table: bool) -> list[CheckResult]:
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

    try:
        df = read_sql(
            f"SELECT database_name, table_name FROM {STREAM_REGISTRY_TABLE} LIMIT 1",
            workgroup="app",
        )
    except Exception as e:
        return CheckResult("glue_get_table_optimizer", FAIL, f"could not read {STREAM_REGISTRY_TABLE}: {e}")

    if df.empty:
        return CheckResult("glue_get_table_optimizer", SKIP, f"no rows in {STREAM_REGISTRY_TABLE} to probe")

    database, table = df.iloc[0]["database_name"], df.iloc[0]["table_name"]
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
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a table")
    args = parser.parse_args()

    mode = get_mode()
    results = _run_checks(mode, args.create_lock_table)
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
