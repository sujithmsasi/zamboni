#!/usr/bin/env bash
# =============================================================================
# Zamboni — Create CloudWatch Alarms
# Run once after initial deployment.
# Requires: AWS CLI, SNS_ALERT_TOPIC_ARN set in environment or .env
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if present
if [ -f "$SCRIPT_DIR/../../.env" ]; then
  set -a; source "$SCRIPT_DIR/../../.env"; set +a
fi

: "${SNS_ALERT_TOPIC_ARN:?SNS_ALERT_TOPIC_ARN not set}"
: "${AWS_REGION:?AWS_REGION not set}"

echo "=== Zamboni — Creating CloudWatch Alarms ==="
echo "    Region   : $AWS_REGION"
echo "    SNS Topic: $SNS_ALERT_TOPIC_ARN"
echo ""

create_alarm() {
  local name="$1"
  local description="$2"
  local metric="$3"
  local namespace="$4"
  local stat="$5"
  local period="$6"
  local eval_periods="$7"
  local threshold="$8"
  local comparison="$9"
  local treat_missing="${10}"
  local dimensions="${11}"

  echo "  [CREATE] $name"

  DIMS_ARG=""
  if [ -n "$dimensions" ]; then
    DIMS_ARG="--dimensions $dimensions"
  fi

  aws cloudwatch put-metric-alarm \
    --alarm-name "$name" \
    --alarm-description "$description" \
    --namespace "$namespace" \
    --metric-name "$metric" \
    --statistic "$stat" \
    --period "$period" \
    --evaluation-periods "$eval_periods" \
    --threshold "$threshold" \
    --comparison-operator "$comparison" \
    --treat-missing-data "$treat_missing" \
    --alarm-actions "$SNS_ALERT_TOPIC_ARN" \
    --ok-actions "$SNS_ALERT_TOPIC_ARN" \
    --region "$AWS_REGION" \
    $DIMS_ARG

  echo "         ✓ Created"
}

# ── HK Engine Alarms ──────────────────────────────────────────────────────────
create_alarm \
  "Zamboni-HK-HighFailureRate" \
  "HK Engine has more than 5 failures in a 1-hour window" \
  "Failed" "Zamboni" "Sum" 3600 1 5 "GreaterThanThreshold" "notBreaching" \
  "Name=Engine,Value=hk"

create_alarm \
  "Zamboni-HK-NoActivity" \
  "HK Engine processed zero tables in 26 hours" \
  "TablesProcessed" "Zamboni" "Sum" 93600 1 0 "LessThanOrEqualToThreshold" "breaching" \
  "Name=Engine,Value=hk"

create_alarm \
  "Zamboni-CircuitBreaker-Trips" \
  "One or more circuit breakers have tripped" \
  "CircuitBreakerTrips" "Zamboni" "Sum" 3600 1 0 "GreaterThanThreshold" "notBreaching" \
  "Name=Engine,Value=hk"

create_alarm \
  "Zamboni-HKCoverage-Low" \
  "HK coverage dropped below 80%" \
  "HKCoverage" "Zamboni" "Average" 86400 1 80 "LessThanThreshold" "notBreaching" \
  ""

# ── Archival Engine Alarms ────────────────────────────────────────────────────
create_alarm \
  "Zamboni-Archival-Failures" \
  "Archival Engine failures — partitions may not have been archived" \
  "Failed" "Zamboni" "Sum" 86400 1 0 "GreaterThanThreshold" "notBreaching" \
  "Name=Engine,Value=archival"

# ── Lifecycle Engine Alarms ───────────────────────────────────────────────────
create_alarm \
  "Zamboni-Lifecycle-Failures" \
  "Lifecycle Engine failures" \
  "Failed" "Zamboni" "Sum" 86400 1 0 "GreaterThanThreshold" "notBreaching" \
  "Name=Engine,Value=lifecycle"

echo ""
echo "=== All alarms created ==="
echo ""
echo "Deploy dashboard:"
echo "  aws cloudwatch put-dashboard \\"
echo "    --dashboard-name Zamboni \\"
echo "    --dashboard-body file://$SCRIPT_DIR/dashboard.json \\"
echo "    --region $AWS_REGION"
