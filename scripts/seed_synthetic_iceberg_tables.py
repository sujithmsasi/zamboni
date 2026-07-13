"""
Zamboni -- Synthetic Iceberg Table Generator (dev/test tooling)

Creates real Iceberg tables in a Glue/Athena database you provide, each
built up through many small INSERT statements so it accumulates realistic
housekeeping problems -- many snapshots, many small data files, and
(optionally) orphan files -- without ever touching a real project table.
Lets you exercise Zamboni's full flow (register -> policy config -> dry
run -> HK Engine compaction/vacuum, archival, lifecycle) against real
Iceberg tables in a disposable dev database.

Requires aws_local or aws_ec2 mode -- there is no real Athena/Iceberg
catalog in ZAMBONI_LOCAL_MODE, so this script refuses to run there.

Usage:
    # Create 10 test tables (dry-run by default -- prints SQL, writes nothing)
    python scripts/seed_synthetic_iceberg_tables.py \\
        --database my_dev_db --s3-location s3://my-dev-bucket/zamboni-test/

    # For real: 10 tables, 15-60 small inserts each (randomized per table)
    python scripts/seed_synthetic_iceberg_tables.py \\
        --database my_dev_db --s3-location s3://my-dev-bucket/zamboni-test/ \\
        --no-dry-run

    # Fewer tables, heavier small-file buildup, plant orphan files too
    python scripts/seed_synthetic_iceberg_tables.py \\
        --database my_dev_db --s3-location s3://my-dev-bucket/zamboni-test/ \\
        --num-tables 3 --min-inserts 40 --max-inserts 80 --orphan-files 5 \\
        --no-dry-run

    # Tear down everything this script created (drops tables + deletes S3 data)
    python scripts/seed_synthetic_iceberg_tables.py \\
        --database my_dev_db --s3-location s3://my-dev-bucket/zamboni-test/ \\
        --cleanup --no-dry-run

After creating tables, bring them into Zamboni the same way a real domain
would be onboarded -- this exercises the registration flow too, not just
the engines:
    python -m engine.cli.register discover --db my_dev_db --out synth.yaml
    python -m engine.cli.register bulk --manifest synth.yaml --no-dry-run
"""
from __future__ import annotations

import argparse
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import APP_ENV, DRY_RUN_DEFAULT, ZAMBONI_LOCAL_MODE, get_mode  # noqa: E402
from engine.utils import glue_client, s3_client  # noqa: E402
from engine.utils.athena_client import run_query  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)

DEFAULT_PREFIX = "zamboni_synthetic_"
WORKGROUP = "app"  # ad-hoc CLI tooling query, matches ATHENA_WORKGROUPS["app"]

EVENT_TYPES = ["signup", "purchase", "refund", "login", "click", "view", "cancel"]


@dataclass
class TableSpec:
    name: str
    insert_count: int
    partition_days_back: int


def _build_specs(num_tables: int, prefix: str, min_inserts: int,
                  max_inserts: int, seed: int | None) -> list[TableSpec]:
    rng = random.Random(seed)
    specs = []
    for i in range(1, num_tables + 1):
        specs.append(TableSpec(
            name=f"{prefix}{i:02d}",
            insert_count=rng.randint(min_inserts, max_inserts),
            partition_days_back=rng.choice([3, 7, 14, 30]),
        ))
    return specs


def _create_table_sql(database: str, table: str, s3_location: str) -> str:
    location = s3_location.rstrip("/") + f"/{table}/"
    return (
        f"CREATE TABLE {database}.{table} (\n"
        f"    id bigint,\n"
        f"    event_type string,\n"
        f"    amount double,\n"
        f"    event_date date\n"
        f")\n"
        f"PARTITIONED BY (event_date)\n"
        f"LOCATION '{location}'\n"
        f"TBLPROPERTIES ('table_type'='ICEBERG', 'format'='parquet')"
    )


def _insert_sql(database: str, table: str, row_id: int, rng: random.Random,
                 days_back: int) -> str:
    event_type = rng.choice(EVENT_TYPES)
    amount = round(rng.uniform(1.0, 999.99), 2)
    event_date = (datetime.now(UTC) - timedelta(days=rng.randint(0, days_back))).date()
    return (
        f"INSERT INTO {database}.{table} "
        f"VALUES ({row_id}, '{event_type}', {amount}, DATE '{event_date.isoformat()}')"
    )


def _plant_orphan_files(bucket: str, prefix: str, table: str, count: int,
                         dry_run: bool) -> None:
    """Write junk objects directly under the table's data/ prefix, bypassing
    Iceberg's transaction log entirely -- these are real orphan files for
    Safe VACUUM's orphan-cleanup path to find, the same way a crashed or
    partially-failed write leaves orphans in a real table."""
    if count <= 0:
        return
    for i in range(count):
        key = f"{prefix.rstrip('/')}/{table}/data/orphan-{i:04d}.parquet"
        if dry_run:
            log.info("synthetic.orphan_file.dry_run", bucket=bucket, key=key)
            continue
        s3_client.put_bytes(bucket, key, b"not a real parquet file -- synthetic orphan for testing")
        log.info("synthetic.orphan_file.written", bucket=bucket, key=key)


def _populate_table(database: str, spec: TableSpec, s3_location: str,
                     orphan_files: int, dry_run: bool, seed: int | None) -> dict:
    rng = random.Random(seed)
    result = {"table": spec.name, "inserts": 0, "errors": []}

    create_sql = _create_table_sql(database, spec.name, s3_location)
    try:
        run_query(create_sql, workgroup=WORKGROUP, database=database, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001 -- report and stop this table, don't abort the whole run
        result["errors"].append(f"CREATE TABLE failed: {e}")
        log.error("synthetic.create_table.failed", table=spec.name, error=str(e))
        return result

    for row_id in range(1, spec.insert_count + 1):
        sql = _insert_sql(database, spec.name, row_id, rng, spec.partition_days_back)
        try:
            run_query(sql, workgroup=WORKGROUP, database=database, dry_run=dry_run)
            result["inserts"] += 1
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"INSERT #{row_id} failed: {e}")
            log.error("synthetic.insert.failed", table=spec.name, row_id=row_id, error=str(e))
            break  # a failed table stays partially built; don't keep piling on

    if orphan_files > 0:
        bucket, base_prefix = s3_client.parse_s3_uri(s3_location)
        _plant_orphan_files(bucket, base_prefix, spec.name, orphan_files, dry_run)

    return result


def create_tables(database: str, s3_location: str, num_tables: int, prefix: str,
                   min_inserts: int, max_inserts: int, orphan_files: int,
                   parallel: int, seed: int | None, dry_run: bool) -> None:
    specs = _build_specs(num_tables, prefix, min_inserts, max_inserts, seed)

    print(f"\n{'DRY RUN -- ' if dry_run else ''}Creating {len(specs)} synthetic "
          f"Iceberg table(s) in {database}:")
    for spec in specs:
        print(f"  {spec.name:<30} ~{spec.insert_count} inserts "
              f"(-> ~{spec.insert_count} snapshots, ~{spec.insert_count} small files), "
              f"partitions spread over {spec.partition_days_back}d")
    if orphan_files:
        print(f"  + {orphan_files} orphan file(s) planted per table")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {
            pool.submit(_populate_table, database, spec, s3_location, orphan_files,
                        dry_run, seed): spec
            for spec in specs
        }
        for future in as_completed(futures):
            results.append(future.result())

    ok = [r for r in results if not r["errors"]]
    failed = [r for r in results if r["errors"]]
    print(f"Done: {len(ok)} table(s) built cleanly, {len(failed)} with errors.")
    for r in failed:
        print(f"  {r['table']}: {r['errors']}")

    if not dry_run and ok:
        print("\nNext step -- bring these into Zamboni for real end-to-end testing:")
        print(f"  python -m engine.cli.register discover --db {database} --out synth.yaml")
        print("  python -m engine.cli.register bulk --manifest synth.yaml --no-dry-run")


def cleanup_tables(database: str, s3_location: str, prefix: str, dry_run: bool) -> None:
    tables = [t for t in glue_client.get_tables(database) if t["Name"].startswith(prefix)]
    if not tables:
        print(f"No tables matching prefix '{prefix}' found in {database}. Nothing to do.")
        return

    print(f"\n{'DRY RUN -- ' if dry_run else ''}Cleaning up {len(tables)} table(s) in {database}:")
    for t in tables:
        print(f"  {t['Name']}")

    bucket, base_prefix = s3_client.parse_s3_uri(s3_location)
    for t in tables:
        name = t["Name"]
        location = glue_client.get_table_location(database, name)
        glue_client.drop_table(database, name, dry_run=dry_run)
        # Explicit S3 sweep regardless of what DROP TABLE does to the data --
        # Athena's DROP TABLE semantics for ICEBERG-type tables don't
        # reliably delete underlying data the way a managed Hive table
        # would, so this doesn't rely on that. Prefer the table's own
        # recorded LOCATION; fall back to the conventional path this script
        # itself uses to create tables, in case the table was already
        # dropped from Glue by something else but data remains.
        prefix_to_sweep = None
        if location and location.startswith("s3://"):
            _, prefix_to_sweep = s3_client.parse_s3_uri(location)
        else:
            prefix_to_sweep = f"{base_prefix.rstrip('/')}/{name}/"
        deleted = s3_client.delete_prefix(bucket, prefix_to_sweep, dry_run=dry_run)
        print(f"  {name}: table dropped, {deleted} S3 object(s) "
              f"{'would be ' if dry_run else ''}deleted")

    print("\nCleanup complete." if not dry_run else "\nDry run only -- nothing was deleted.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create/tear down synthetic Iceberg tables for end-to-end Zamboni testing.")
    parser.add_argument("--database", required=True,
                         help="Glue/Athena database to create tables in (must already exist).")
    parser.add_argument("--s3-location", required=True,
                         help="S3 URI prefix for table data, e.g. s3://my-dev-bucket/zamboni-test/")
    parser.add_argument("--num-tables", type=int, default=10)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX,
                         help=f"Table name prefix (default: {DEFAULT_PREFIX})")
    parser.add_argument("--min-inserts", type=int, default=15)
    parser.add_argument("--max-inserts", type=int, default=60)
    parser.add_argument("--orphan-files", type=int, default=0,
                         help="Orphan (untracked) files to plant per table, for testing "
                              "Safe VACUUM's orphan-cleanup path.")
    parser.add_argument("--parallel", type=int, default=4,
                         help="Tables to build concurrently (default 4).")
    parser.add_argument("--seed", type=int, default=None,
                         help="Random seed for reproducible table specs/data.")
    parser.add_argument("--cleanup", action="store_true",
                         help="Drop all tables matching --prefix in --database and delete "
                              "their S3 data, instead of creating anything.")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=DRY_RUN_DEFAULT)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument("--yes", action="store_true",
                         help="Skip the interactive confirmation prompt.")
    args = parser.parse_args()

    # Hard, unconditional block -- no override flag, by design. This tool
    # creates and deletes real data; dev/preprod are the user's call to
    # manage, but production must never be reachable at all, regardless of
    # what --database/--s3-location/--yes are passed.
    if APP_ENV.strip().lower() in ("prod", "production"):
        print(f"APP_ENV={APP_ENV!r} -- refusing to run against a production "
              "environment. This script creates and deletes real S3 data and "
              "must never touch prod, no matter what --database/--s3-location "
              "are passed. There is no override for this check.")
        sys.exit(1)

    if ZAMBONI_LOCAL_MODE:
        print("ZAMBONI_LOCAL_MODE=true -- there is no real Athena/Iceberg catalog to "
              "create tables in. Run this against aws_local or aws_ec2 mode instead.")
        sys.exit(1)

    mode = get_mode()
    action = "DROP + delete S3 data for" if args.cleanup else "CREATE"
    print(f"Mode: {mode}")
    print(f"About to {action} tables matching prefix '{args.prefix}' "
          f"in database '{args.database}' (data under {args.s3_location}).")
    if not args.dry_run and not args.yes:
        confirm = input("Type the database name to confirm: ").strip()
        if confirm != args.database:
            print("Confirmation did not match. Aborting -- nothing was done.")
            sys.exit(1)

    if args.cleanup:
        cleanup_tables(args.database, args.s3_location, args.prefix, args.dry_run)
    else:
        create_tables(args.database, args.s3_location, args.num_tables, args.prefix,
                       args.min_inserts, args.max_inserts, args.orphan_files,
                       args.parallel, args.seed, args.dry_run)


if __name__ == "__main__":
    main()
