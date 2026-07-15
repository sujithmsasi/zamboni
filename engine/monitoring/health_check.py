"""
Zamboni — System Health Check
Run before every engine invocation to verify AWS connectivity.
Also used by the CodeDeploy app_start.sh hook.

Checks:
  - Athena connectivity (SELECT 1)
  - S3 metadata bucket accessible
  - Glue catalog reachable
  - SNS topic exists
  - stream_registry table exists
"""
from dataclasses import dataclass, field

from config.settings import (
    ATHENA_RESULTS_BUCKET,
    AWS_REGION,
    SNS_ALERT_TOPIC_ARN,
    STREAM_REGISTRY_TABLE,
    ZAMBONI_METADATA_BUCKET,
    get_boto3_session,
)
from engine.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class HealthCheckResult:
    passed:   bool = True
    checks:   list[dict] = field(default_factory=list)
    errors:   list[str]  = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        self.checks.append({"check": name, "status": status, "detail": detail})
        if not ok:
            self.passed = False
            self.errors.append(f"{name}: {detail}")

    def summary(self) -> str:
        total  = len(self.checks)
        passed = sum(1 for c in self.checks if c["status"] == "PASS")
        return f"{passed}/{total} checks passed"


def run_health_check(verbose: bool = False) -> HealthCheckResult:
    """
    Run all system health checks.
    Returns HealthCheckResult with pass/fail per check.
    """
    result = HealthCheckResult()

    # ── 1. Athena connectivity ─────────────────────────────────────────────────
    try:
        client   = get_boto3_session().client("athena", region_name=AWS_REGION)
        query_id = client.start_query_execution(
            QueryString="SELECT 1",
            WorkGroup="zamboni-standard",
            ResultConfiguration={"OutputLocation": ATHENA_RESULTS_BUCKET},
        )["QueryExecutionId"]

        import time
        for _ in range(10):
            state = client.get_query_execution(
                QueryExecutionId=query_id
            )["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Query {state}")
            time.sleep(2)

        result.add("Athena Connectivity", True, f"WorkGroup: zamboni-standard, QueryId: {query_id[:8]}...")
    except Exception as e:
        result.add("Athena Connectivity", False, str(e))

    # ── 2. S3 — metadata bucket ────────────────────────────────────────────────
    try:
        s3 = get_boto3_session().client("s3", region_name=AWS_REGION)
        bucket, prefix = _parse_s3(ZAMBONI_METADATA_BUCKET)
        s3.head_bucket(Bucket=bucket)
        result.add("S3 Metadata Bucket", True, bucket)
    except Exception as e:
        result.add("S3 Metadata Bucket", False, str(e))

    # ── 3. S3 — Athena results bucket ─────────────────────────────────────────
    try:
        bucket, _ = _parse_s3(ATHENA_RESULTS_BUCKET)
        get_boto3_session().client("s3", region_name=AWS_REGION).head_bucket(Bucket=bucket)
        result.add("S3 Athena Results Bucket", True, bucket)
    except Exception as e:
        result.add("S3 Athena Results Bucket", False, str(e))

    # ── 4. Glue catalog ───────────────────────────────────────────────────────
    try:
        glue = get_boto3_session().client("glue", region_name=AWS_REGION)
        glue.get_databases(MaxResults=1)
        result.add("Glue Catalog", True, "Reachable")
    except Exception as e:
        result.add("Glue Catalog", False, str(e))

    # ── 5. stream_registry table ──────────────────────────────────────────────
    try:
        from engine.utils.athena_client import read_sql
        df = read_sql(
            f"SELECT COUNT(*) AS cnt FROM {STREAM_REGISTRY_TABLE}",
            workgroup="app",
        )
        count = int(df.iloc[0]["cnt"]) if not df.empty else 0
        result.add("Stream Registry Table", True, f"{count:,} tables registered")
    except Exception as e:
        result.add("Stream Registry Table", False, str(e))

    # ── 6. SNS topic ──────────────────────────────────────────────────────────
    try:
        sns = get_boto3_session().client("sns", region_name=AWS_REGION)
        sns.get_topic_attributes(TopicArn=SNS_ALERT_TOPIC_ARN)
        result.add("SNS Alert Topic", True, SNS_ALERT_TOPIC_ARN.split(":")[-1])
    except Exception as e:
        result.add("SNS Alert Topic", False, str(e))

    log.info(
        "health_check.complete",
        passed=result.passed,
        summary=result.summary(),
        errors=result.errors,
    )
    return result


def print_health_check(result: HealthCheckResult) -> None:
    """Print health check results to console with color."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    t       = Table(show_header=True, header_style="bold blue")
    t.add_column("Check",  style="white")
    t.add_column("Status", width=8)
    t.add_column("Detail", style="dim")

    for check in result.checks:
        status = "[green]PASS[/]" if check["status"] == "PASS" else "[red]FAIL[/]"
        t.add_row(check["check"], status, check.get("detail", ""))

    console.print(t)
    if result.passed:
        console.print(f"\n[green]✓ All checks passed[/] — {result.summary()}")
    else:
        console.print(f"\n[red]✗ {result.summary()}[/]")
        for err in result.errors:
            console.print(f"  [red]•[/] {err}")


def _parse_s3(uri: str) -> tuple[str, str]:
    assert uri.startswith("s3://"), f"Not an S3 URI: {uri}"
    parts = uri[5:].split("/", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")
