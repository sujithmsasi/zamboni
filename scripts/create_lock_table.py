"""
Zamboni -- Create DynamoDB Lock Table
Idempotent creation of the zamboni_maintenance_locks table used by
engine.core.lock_service.LockService in aws_local/aws_ec2 mode. This is the
Phase 1a fallback for the DynamoDB resource Phase 6 will add to the
CloudFormation stack (contracts.md §3.1, §8) -- no CFN template exists yet.

Usage:
    python scripts/create_lock_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AWS_REGION, DDB_LOCK_TABLE, get_boto3_session  # noqa: E402


def create_lock_table() -> None:
    client = get_boto3_session().client("dynamodb", region_name=AWS_REGION)

    existing = client.list_tables().get("TableNames", [])
    if DDB_LOCK_TABLE in existing:
        print(f"Table {DDB_LOCK_TABLE!r} already exists -- nothing to do.")
        return

    client.create_table(
        TableName=DDB_LOCK_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "table_fqn", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "table_fqn", "KeyType": "HASH"}],
    )
    client.get_waiter("table_exists").wait(TableName=DDB_LOCK_TABLE)
    client.update_time_to_live(
        TableName=DDB_LOCK_TABLE,
        TimeToLiveSpecification={"AttributeName": "expires_at", "Enabled": True},
    )
    print(f"Created table {DDB_LOCK_TABLE!r} with TTL on 'expires_at'.")


if __name__ == "__main__":
    create_lock_table()
