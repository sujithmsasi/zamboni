"""
Zamboni — Compaction Glue Job
Handles sort and zorder compaction strategies via PySpark.
Deployed to S3 and triggered by the HK Engine via glue.start_job_run().

Job Parameters (passed by engine/operations/compaction.py):
    --strategy:            sort | zorder
    --catalog:             Glue catalog name
    --database:            Glue database name
    --table:               Table name
    --table_fqn:           Full table FQN (for logging)
    --sort_columns:        Comma-separated sort columns (sort strategy)
    --zorder_columns:      Comma-separated zorder columns (zorder strategy)
    --target_file_size_mb: Target file size after compaction
    --worker_type:         G.1X | G.2X | G.4X
    --num_workers:         Number of workers
    --execution_class:     FLEX | STANDARD
    --partition_filter:    Optional SQL partition filter

Deployment:
    Upload this file to S3 and reference in the Glue job definition.
    S3 path: s3://your-zamboni-metadata-bucket/glue_jobs/zamboni_compaction.py
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import SparkSession


def get_spark_session(catalog: str) -> SparkSession:
    sc = SparkContext.getOrCreate()
    glue_context = GlueContext(sc)
    spark = glue_context.spark_session
    spark.conf.set("spark.sql.extensions",
                   "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    spark.conf.set(f"spark.sql.catalog.{catalog}",
                   "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set(f"spark.sql.catalog.{catalog}.catalog-impl",
                   "org.apache.iceberg.aws.glue.GlueCatalog")
    spark.conf.set(f"spark.sql.catalog.{catalog}.io-impl",
                   "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set("spark.sql.iceberg.handle-timestamp-without-timezone", "true")
    return spark


def run_sort_compaction(
    spark: SparkSession,
    catalog: str,
    database: str,
    table: str,
    sort_columns: list,
    target_file_size_mb: int,
    partition_filter: str = None,
) -> None:
    """Sort compaction — rewrites files ordered by sort_columns."""
    sort_cols_str = ", ".join(sort_columns)

    print(f"[sort] Compacting {catalog}.{database}.{table}")
    print(f"[sort] Sort columns: {sort_cols_str}")
    print(f"[sort] Target file size: {target_file_size_mb}MB")

    spark.sql(f"""
        CALL {catalog}.system.rewrite_data_files(
            table => '{database}.{table}',
            strategy => 'sort',
            sort_order => '{sort_cols_str}',
            options => map(
                'target-file-size-bytes', '{target_file_size_mb * 1024 * 1024}',
                'partial-progress.enabled', 'true',
                'partial-progress.max-commits', '10'
            )
            {f", where => '{partition_filter}'" if partition_filter else ""}
        )
    """).show(truncate=False)


def run_zorder_compaction(
    spark: SparkSession,
    catalog: str,
    database: str,
    table: str,
    zorder_columns: list,
    target_file_size_mb: int,
    partition_filter: str = None,
) -> None:
    """Z-order compaction — multi-dimensional clustering by zorder_columns."""
    zorder_cols_str = ", ".join(zorder_columns)

    print(f"[zorder] Compacting {catalog}.{database}.{table}")
    print(f"[zorder] Z-order columns: {zorder_cols_str}")
    print(f"[zorder] Target file size: {target_file_size_mb}MB")

    spark.sql(f"""
        CALL {catalog}.system.rewrite_data_files(
            table => '{database}.{table}',
            strategy => 'sort',
            sort_order => 'zorder({zorder_cols_str})',
            options => map(
                'target-file-size-bytes', '{target_file_size_mb * 1024 * 1024}',
                'partial-progress.enabled', 'true',
                'partial-progress.max-commits', '10'
            )
            {f", where => '{partition_filter}'" if partition_filter else ""}
        )
    """).show(truncate=False)


def main():
    # ── Parse job args ────────────────────────────────────────────────────────
    required_args = [
        "strategy", "catalog", "database", "table", "table_fqn",
        "target_file_size_mb",
    ]
    optional_args = ["sort_columns", "zorder_columns", "partition_filter"]

    args = getResolvedOptions(sys.argv, required_args)

    # Optional args
    all_argv_keys = [a.lstrip("-").replace("-", "_") for a in sys.argv if a.startswith("--")]
    for opt in optional_args:
        if opt in all_argv_keys:
            args.update(getResolvedOptions(sys.argv, [opt]))
        else:
            args[opt] = None

    strategy            = args["strategy"]
    catalog             = args["catalog"]
    database            = args["database"]
    table               = args["table"]
    table_fqn           = args["table_fqn"]
    target_file_size_mb = int(args["target_file_size_mb"])
    partition_filter    = args.get("partition_filter")

    print("[zamboni_compaction] Starting job")
    print(f"  strategy   : {strategy}")
    print(f"  table      : {table_fqn}")
    print(f"  target_mb  : {target_file_size_mb}")
    print(f"  filter     : {partition_filter or 'none'}")

    # ── Spark session ─────────────────────────────────────────────────────────
    spark = get_spark_session(catalog)
    job   = Job(GlueContext(SparkContext.getOrCreate()))
    job.init(f"zamboni_compaction_{table}", args)

    # ── Run strategy ──────────────────────────────────────────────────────────
    if strategy == "sort":
        sort_columns = args.get("sort_columns", "").split(",")
        sort_columns = [c.strip() for c in sort_columns if c.strip()]
        if not sort_columns:
            raise ValueError("--sort_columns required for sort strategy")
        run_sort_compaction(
            spark, catalog, database, table,
            sort_columns, target_file_size_mb, partition_filter
        )

    elif strategy == "zorder":
        zorder_columns = args.get("zorder_columns", "").split(",")
        zorder_columns = [c.strip() for c in zorder_columns if c.strip()]
        if not zorder_columns:
            raise ValueError("--zorder_columns required for zorder strategy")
        run_zorder_compaction(
            spark, catalog, database, table,
            zorder_columns, target_file_size_mb, partition_filter
        )

    else:
        raise ValueError(f"Unknown strategy: {strategy}. Expected: sort | zorder")

    job.commit()
    print(f"[zamboni_compaction] Job complete for {table_fqn}")


if __name__ == "__main__":
    main()
