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
  gate0_override_max_hours: number;
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

// ── locks ─────────────────────────────────────────────────────────────────────

export interface RescanResult {
  scanned: number;
  conflicts: number;
  audit_id: string;
}

// ── costs ─────────────────────────────────────────────────────────────────────

export interface CostTotals {
  estimated_athena_cost_usd: number | null;
  gb_scanned: number | null;
  gb_compacted: number | null;
  gb_archived: number | null;
}

export interface CostGroupRow {
  cost_usd: number;
  gb_scanned: number;
  [key: string]: unknown;
}

export interface CostsResponse {
  group_by: string;
  totals: CostTotals;
  by_group: CostGroupRow[];
  cost_explorer_enabled: boolean;
  live_billing?: Record<string, unknown>;
}

// ── audit ─────────────────────────────────────────────────────────────────────

export interface AuditRow {
  audit_id: string;
  timestamp: string;
  actor: string;
  action_type: string;
  page_source: string;
  target_type: string;
  target_id: string;
  domain: string;
  environment: string;
  dry_run: boolean;
  status: string;
  reason: string;
  ticket_number: string;
  before_value: string;
  after_value: string;
  error_message: string;
  [key: string]: unknown;
}

// ── dry run viewer ────────────────────────────────────────────────────────────

export interface DryRunGates {
  gate1_enabled: boolean;
  gate2_enabled: boolean;
  gate3_enabled: boolean;
  upstream_job: string | null;
  window_decision: string;
}

export interface DryRunView {
  table_fqn: string;
  registration: Record<string, unknown>;
  config: Record<string, unknown>;
  gates: DryRunGates;
  planned_sql: string | null;
}

// ── gates ─────────────────────────────────────────────────────────────────────

export interface GatesInfo {
  table_fqn: string;
  gate1_enabled: boolean;
  gate2_enabled: boolean;
  gate3_enabled: boolean;
  gate0_override_until: string | null;
  gate0_override_reason: string | null;
  gate0_override_by: string | null;
  conflict_cache: Record<string, unknown> | null;
}

// ── glue browse / control-m jobs (Table Registration, Wave 2) ────────────────

export interface GlueTableRow {
  name: string;
  table_fqn: string;
  format: string;
  registered: boolean;
}

export interface JobRow {
  job_name: string;
  job_type: string;
  domain: string;
  description: string;
  expected_start_time: string;
  expected_duration_min: number;
  job_frequency: string;
  active: boolean;
  [key: string]: unknown;
}

export interface RegisterResult {
  success: boolean;
  dry_run: boolean;
  audit_id: string | null;
  template: string;
}

export interface JobMappingRow {
  job: string;
  job_type: string;
  domain: string;
  layer: string;
  database_name: string;
  table_pattern: string;
  tables_matched: number;
}

// ── policies / templates (Policy Configuration, Wave 2) ──────────────────────

export interface PolicyViewRow {
  table_fqn: string;
  domain: string;
  layer: string;
  tier: string;
  policy_template: string | null;
  compaction_strategy: string | null;
  compaction_engine: string | null;
  compaction_target_file_size_mb: number | null;
  snapshot_retention_days: number | null;
  snapshot_min_to_keep: number | null;
  orphan_file_retention_days: number | null;
  run_frequency: string | null;
  gate1_enabled: boolean | null;
  gate2_enabled: boolean | null;
  gate3_enabled: boolean | null;
  manually_overridden: boolean | null;
  [key: string]: unknown;
}

export interface WindowConfig {
  type: 'post_batch' | 'scheduled';
  timezone: string;
  delay_minutes: number;
  start_time: string;
  duration_hours: number;
  blackout_hours: number[];
}

// GET /api/policies/{fqn} -- raw hk_config row. window_config comes back as
// a JSON-encoded string (SQLite TEXT column, same as Streamlit's json.loads
// pattern) -- parse client-side, don't add a server-side reshape for this.
export interface PolicyDetail {
  table_fqn: string;
  policy_template: string | null;
  compaction_strategy: string;
  compaction_engine: string;
  compaction_target_file_size_mb: number;
  snapshot_retention_days: number;
  snapshot_min_to_keep: number;
  orphan_file_retention_days: number;
  orphan_cleanup_cadence_days: number;
  run_frequency: string;
  sort_order_cols: string | null;
  partition_column: string | null;
  partition_type: string;
  window_config: string | null;
  manually_overridden: boolean;
  gate1_enabled: boolean;
  gate2_enabled: boolean;
  gate3_enabled: boolean;
  [key: string]: unknown;
}

export interface PolicyTemplate {
  description: string;
  compaction_strategy: string;
  compaction_engine: string;
  compaction_target_file_size_mb: number;
  snapshot_retention_days: number;
  snapshot_min_to_keep: number;
  orphan_file_retention_days: number;
  run_frequency: string;
  gate1_enabled?: boolean;
  gate2_enabled?: boolean;
  gate3_enabled?: boolean;
  window_config?: WindowConfig | Record<string, unknown>;
}

export type TemplatesMap = Record<string, PolicyTemplate>;

// ── domains ───────────────────────────────────────────────────────────────────

export interface DomainRow {
  domain_name: string;
  display_name: string;
  description: string;
  owner_name: string;
  owner_email: string;
  team_name: string;
  ci_number: string;
  archive_enabled: boolean;
  hot_retention_days: number;
  archive_duration_days: number;
  stale_threshold_days: number;
  auto_delete_after_days: number;
  is_active: boolean;
  digest_enabled: boolean;
  digest_email: string | null;
  notes: string;
  registered_at: string;
  table_count?: number;
  [key: string]: unknown;
}
