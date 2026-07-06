// Mirrors api/models.py + api/services/*.py response shapes (contracts.md §6).
// LOCK: keep snake_case in TS to match Pydantic/DataFrame field names exactly —
// no camelCase remapping layer, so a field rename on the Python side is a
// find-and-replace here rather than a silent mismatch. See ui/PATTERN.md.

export interface ErrorDetail {
  code: string;
  message: string;
}

export interface Pagination {
  page: number;
  size: number;
  total: number;
}

export interface Envelope<T> {
  data: T;
  pagination: Pagination | null;
  error: ErrorDetail | null;
}

export interface MutationResult {
  success: boolean;
  dry_run: boolean;
  audit_id: string | null;
}

// ── system ───────────────────────────────────────────────────────────────────

export interface SystemMode {
  mode: string;
  app_env: string;
  dry_run_default: boolean;
  user: string;
}

export interface LockRow {
  table_fqn: string;
  lock_owner: string;
  operation: string;
  acquired_at: string;
  heartbeat_at: string;
  expires_at: number;
}

// ── health / executions ──────────────────────────────────────────────────────

export interface ConflictSummary {
  total: number;
  scanned: number;
  conflicted: number;
  stale_cache: number;
  overridden: number;
}

export interface CoverageByDomain {
  domain: string;
  total: number;
  enabled: number;
  pct_enabled: number;
}

export interface ExecutionTrendPoint {
  execution_date: string;
  status: string;
  count: number;
}

export interface ReclaimedStoragePoint {
  day: string;
  vacuum_gb: number;
  archived_gb: number;
}

export interface TopReclaimTable {
  table_fqn: string;
  domain: string;
  gb_reclaimed: number;
}

export interface CostTrendPoint {
  execution_date: string;
  cost_usd: number;
}

export interface StorageSavings {
  total_gb_reclaimed: number;
  estimated_monthly_savings_usd: number;
}

export interface FlaggedTable {
  table_fqn: string;
  domain: string;
  layer: string;
  tier: string;
  status: 'AT_RISK' | 'NEEDS_ATTENTION';
  reason: string;
}

export interface FleetHealth {
  healthy: number;
  needs_attention: number;
  at_risk: number;
  tables: FlaggedTable[];
}

export interface NonprodFunnelPoint {
  lifecycle_state: string;
  count: number;
}

export interface DryRunAdoptionRow {
  domain: string;
  tables_in_dry_run: number;
  max_days_waiting: number;
  stale: boolean;
}

export interface KpiSet {
  total_registered: number;
  hk_enabled: number;
  iceberg_tables: number;
  in_dry_run: number;
  failures_7d: number;
  conflicts: ConflictSummary;
  coverage_by_domain: CoverageByDomain[];
  execution_trend: ExecutionTrendPoint[];
  reclaimed_storage_trend: ReclaimedStoragePoint[];
  top_tables_by_reclaim: TopReclaimTable[];
  cost_trend: CostTrendPoint[];
  storage_savings: StorageSavings;
  fleet_health: FleetHealth;
  nonprod_funnel: NonprodFunnelPoint[];
  dry_run_adoption: DryRunAdoptionRow[];
}

export interface ExecutionRow {
  execution_id: string;
  table_fqn: string;
  engine: string;
  operation: string;
  status: string;
  dry_run: boolean;
  started_at: string;
  completed_at: string | null;
  integrity_status: string | null;
  [key: string]: unknown;
}

// ── tables / conflicts (extend per need — Waves 1-2) ─────────────────────────

export interface TableRow {
  table_fqn: string;
  domain: string;
  layer: string;
  tier: string;
  environment: string;
  table_format: string;
  hk_enabled: boolean;
  archive_enabled: boolean;
  lifecycle_enabled: boolean;
  [key: string]: unknown;
}

export interface ConflictRow {
  table_fqn: string;
  domain: string;
  layer: string;
  tier: string;
  aws_opt_compaction: boolean | null;
  aws_opt_retention: boolean | null;
  aws_opt_orphan: boolean | null;
  aws_opt_checked_at: string | null;
  gate0_override_until: string | null;
  [key: string]: unknown;
}
