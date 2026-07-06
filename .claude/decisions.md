# Zamboni — Architecture Decisions

This file records decisions inferred from the current code (first-written
2026-07-05, Phase 0 — no prior version existed in git history). Locked
replatform decisions (D1-D6) live in `.claude/contracts.md`, not here — this
file is for decisions already baked into the shipped engine/app, plus notes
on where a replatform decision needs reconciling against them.

## Vacuum: single bare VACUUM, not separate expire/orphan calls
Athena engine v3's `VACUUM db.table;` handles snapshot expiry AND orphan file
removal in one call (`engine/operations/vacuum.py:1-20`). There is no
separate "delete orphans older than X hours" call — retention is entirely
TBLPROPERTIES-driven, set ahead of time by `property_sync.py`. Decided this
way because Athena v3 genuinely does not support VACUUM clauses/options
(bare syntax only) — the code comments call this out as a hard rule, not a
stylistic choice.
> REALITY vs contracts.md §5: the orchestrator's "REMOVE ORPHANS — two-phase"
> step (estimate scope → sanity check → delete with `older_than`) assumes an
> orphan-only call that can take an age parameter. That call doesn't exist
> in this codebase's Athena v3 model. Needs Sujith's decision on how Gate 0's
> orphan-sanity-abort logic attaches to a VACUUM call that always does both
> snapshot expiry and orphan removal together.

## Idempotency key excludes run_id by design
`engine/core/idempotency.py:31-63` hashes `table_fqn|operation|window_id` and
deliberately excludes `run_id` so that two different trigger paths
(EventBridge safety-net vs Control-M) processing the same table in the same
window produce the same `execution_id` and dedupe correctly. This is a
documented, intentional tradeoff (see the module docstring) — don't "fix" it
by adding run_id back into the hash.

## Backward-compatible column probing instead of migrations
`idempotency.py` and `property_sync.py` both catch failures, inspect the
error string for `"column" ... "<name>"`, and silently no-op if a column is
missing, rather than requiring a migration to run first. This lets the same
code run against environments at different schema versions. New columns
added for Workstream A (lock_id, metadata_location_before/after, etc.)
should follow this same probe-and-no-op pattern in any code that might run
before the `ALTER TABLE ADD COLUMNS` has been applied everywhere.

## EXECUTION_LOG_MODE: parquet path is best-effort, insert is the fallback of record
`engine/core/execution_log_parquet.py` never raises on `auto`/`both` modes —
any Parquet/add_files failure falls back to the row-by-row Athena INSERT in
`execution_log.py`. Only `EXECUTION_LOG_MODE=parquet` (strict) propagates the
error. Any schema change (new columns) must be added to both paths or the
fallback will silently write fewer columns than the primary path.

## Circuit breaker signature
`engine/core/circuit_breaker.py::trip(table_fqn, failure_count, dry_run)` —
contracts.md §5 refers to `circuit_breaker.trip(fqn)` (single arg). The
existing function requires `failure_count`; Phase 1b's orchestrator will need
to pass it through (e.g. from the failed verify step) rather than calling
with just the fqn.

## Local mode is a real SQLite shim, not a mock
`ZAMBONI_LOCAL_MODE=true` (`config/settings.py:118`) switches to a SQLite
database (`zamboni_local.db`) via `engine/utils/local_db.py`, which
translates Athena/Trino SQL (DATE_DIFF, DATE_TRUNC, etc.) into SQLite
equivalents with a hand-written char-scanner (not regex) for nested-paren
handling. This is a load-bearing dev/demo path, not a test double — treat it
as a real backend when reasoning about behavior in `ZAMBONI_LOCAL_MODE`.

## Phase 1b: §5-A orphan-estimation approach actually used
`engine/core/maintenance_ops.py::_preflight_sanity()` computes
`would_expire_pct` as `COUNT(snapshots older than the clamped floor) /
COUNT(total snapshots)` via a single query against Iceberg's `"$snapshots"`
metadata table — this is the literal reading of contracts.md §5-A step b,
not an approximation of file-level scope. A true *file*-level estimate
(files referenced only by snapshots about to expire) would require walking
manifest lists per candidate snapshot, which Athena's `$files`/$snapshots`
metadata views don't expose directly and which the existing `vacuum.py`
gap-fix set (1,2,3,9,10) has no primitive for. Snapshot-count-based scope is
the same signal `health_checker.py::_check_snapshots` already uses for
`expired_snapshots`, so this reuses an established approximation rather than
inventing a new one.
In `ZAMBONI_LOCAL_MODE`, `"$snapshots"`/`"$files"` have no SQLite
equivalent (see the "Local mode is a real SQLite shim" note above — the
shim translates Athena SQL syntax, it does not fabricate Iceberg metadata
tables). `read_sql_local()` returns an empty DataFrame for these queries,
so `_preflight_sanity()` reports `would_expire_pct=0.0` (nothing to abort
on) — matching how `health_checker.py` already treats an empty `$snapshots`
result as "nothing to flag" in local mode. Confirmed via a real local
dry run against the seeded DB (see Migration Progress entry).

## Phase 1b: 72h floor = rollback window rationale
`ORPHAN_MIN_AGE_HOURS_FLOOR=72` (contracts.md D2) is enforced in
`maintenance_ops.py::_clamp_vacuum_properties()` by setting
`vacuum_max_snapshot_age_seconds` on the table to at least 72h worth of
seconds before every VACUUM call — this value is deliberately never used as
a one-off delete-time argument (Athena engine v3 VACUUM has none) but as a
*standing table property* that persists between runs. The reasoning: since
a single combined VACUUM both expires snapshots and removes the files only
those snapshots reference, the floor is the guaranteed minimum time window
during which a bad commit's prior snapshot is still recoverable via Iceberg
time-travel / rollback before its files can be physically deleted. Widening
the floor (e.g. `ORPHAN_DEFAULT_AGE_HOURS=96`) buys a longer recovery
window at the cost of slower orphan reclamation — the floor is a lower
bound, not a target.

## Phase 1b: run_expire()/run_orphan_delete() collapsed into run_safe_vacuum()
The phase brief's intro paragraph asked for `maintenance_ops.py` to expose
`run_optimize()`, `run_expire()`, `run_orphan_delete()`. The CRITICAL
OVERRIDE in the phase prompt makes contracts.md §5-A (single combined bare
`VACUUM`, no separable orphan-only call) supersede that framing wherever it
conflicts — and it conflicts here, since there is nothing for a standalone
`run_orphan_delete()` to call that `run_expire()` wouldn't also call.
`engine/core/maintenance_ops.py` exposes `run_optimize()` and
`run_safe_vacuum()` instead — the latter performing all of §5-A's a→d
sequence (clamp → sanity → VACUUM → post-audit) as one atomic step. No dead
aliases were added for the unused `run_expire`/`run_orphan_delete` names.

## Phase 1b: gates 1-4 duplicated (not extracted) into orchestrator.py
`engine/core/orchestrator.py::run_table_maintenance()` must be independently
callable (contracts.md §5's acceptance CLI) and therefore re-implements
Gate 1 (Control-M), Gate 2 (window), idempotency, Gate 3 (frequency), and
Gate 4 (circuit breaker) rather than calling into
`hk_engine.py::_run_gates_and_operations()`, which is tightly coupled to
`HKEngine` instance state (`self._write_log`, `self._log_buffer`) and mixes
gates with the legacy operations flow this phase replaces. Phase 1a already
established the norm of not re-indenting large existing blocks (it split
Gate 0 into `_run_gates_and_operations` specifically to avoid re-indenting
~330 lines). Extracting `is_due()` to module level was safe and done (both
`hk_engine.py` and `orchestrator.py` now share it); extracting the rest
would have meant restructuring ~130 tested lines for one new caller, so the
duplication was kept — bounded to straightforward conditional checks, never
the safety-critical operations/verification logic.

## Phase 1b: pre-existing execution_log local-schema column drift (found, not fixed)
Running a real local dry run through `engine/core/orchestrator.py` surfaces
(as a caught, logged, non-fatal error) `"table execution_log has 37 columns
but 38 values were supplied"` on every `execution_log.write()` call in
`ZAMBONI_LOCAL_MODE`. This predates Phase 1b: `scripts/seed_local_db.py`'s
`execution_log` SQLite DDL + migrations are missing `partition_date`,
`archive_s3_path`, `pre_validation`, `post_validation` (all present in the
Athena DDL and in `execution_log.py::write()`'s positional INSERT) while
carrying three extra local-only columns (`vacuum_iterations`,
`oldest_snapshot_id`, `newest_snapshot_id`) that `write()` never
references. Nothing before Phase 1b ever exercised a real positional
INSERT against the seeded local `execution_log` table (existing tests
mock `read_sql`/`run_query` above this layer), so the drift was latent.
Out of scope here — flagged for a follow-up fix (either realign the local
DDL positionally, or move `execution_log.write()` to a named-column
INSERT so schema order stops being load-bearing).

## Phase 1c: 72h floor = guaranteed rollback window (showcase copy)
`engine/core/recovery.py::ORPHAN_REFUSAL_REASON` cites
`ORPHAN_MIN_AGE_HOURS_FLOOR` (72h) verbatim, not a generic "not found"
message. This is intentional, exact showcase copy, not an implementation
detail: the property clamp Phase 1b's `_clamp_vacuum_properties()` applies
before every SAFE-VACUUM guarantees `vacuum_max_snapshot_age_seconds` is at
least 72h worth of seconds, so Athena's combined VACUUM (contracts.md §5-A)
cannot physically delete the files backing a snapshot younger than that —
which means **the 72h floor IS the guaranteed rollback window**, not just a
retention setting. `validate_rollback_target()`'s refusal message makes that
guarantee legible to an operator at the exact moment it matters, and doubles
as the VP-facing showcase line (contracts.md D2's rationale). See
`docs/runbooks/metadata_recovery.md` for the full explanation.

## Phase 1c: local-mode validate/rollback simulation (documented gap, same pattern as capture_state)
`engine/core/recovery.py`'s `_validate_local()`/`_rollback_local()` do not
call real S3/Glue — there is no live catalog or S3 bucket in
`ZAMBONI_LOCAL_MODE` to check against, the same documented gap
`integrity_checker.capture_state()` already carries for
`metadata_location`. Simulation rules (deliberately simple, not a
general-purpose fake AWS):
  - `validate_rollback_target()`: a `metadata_location` containing the
    literal substring `"_orphaned"` simulates a missing/orphan-deleted
    target (exercises the refusal path without real AWS); anything else
    simulates a present, valid metadata.json with a deterministic fake
    `snapshot_id` (`hash((fqn, metadata_location))`).
  - `rollback_metadata()`'s local write path targets a new
    `stream_registry.metadata_location` column (`scripts/seed_local_db.py`
    migration) that is **local-simulation-only** — it is NOT part of
    contracts.md §3.2's locked Athena DDL. In real mode the current pointer
    always comes from live Glue `Parameters.metadata_location`, never from
    `stream_registry`, so there is no schema conflict between the two.
  - The data-file spot-check (`_spot_check_data_files()`) lazily imports
    `fastavro` (added to `requirements.txt`, matching the existing
    optional-dependency pattern `itables` already uses in
    `app/components/grid_utils.py`) and degrades to "skipped" rather than
    failing validation if it isn't installed — both Iceberg manifest-lists
    and manifest files are Avro, and reading them is genuinely best-effort
    per the phase brief ("capped").

## Phase 3: "Zamboni Arctic Blue" theme supersedes contracts §7's starting tokens
`.claude/ui_design.md` (approved after Phase 3's initial build-and-sign-off
round) is a full visual-system spec — dark navy Sider, Ant Design v5 tokens,
Phosphor duotone icons, mint-green dry-run/governance surfaces, coastal-
palette KPI cards — and supersedes the light-sidebar starting-point
`theme.ts` contracts.md §7 shipped with (that doc explicitly allowed "one
adjustment round" during acceptance; this was a full swap, not a tweak, so
recorded here rather than silently overwriting the locked snippet). No
change to information architecture, routing, or data fetching — visual
layer only, per `ui_design.md`'s own IMPLEMENTATION REQUIREMENTS section.

## Phase 3: icon library is Phosphor, not Ant Design Icons
`ui_design.md` mandates `@phosphor-icons/react` (duotone weight) for sidebar
icons and explicitly forbids mixing icon families. `@ant-design/icons` was
removed from `ui/package.json` entirely (antd still pulls its own copy
internally for built-in chrome like Table sort arrows) rather than left as
an unused dependency, since nothing in `ui/src` imports it anymore
(CsvButtons' upload/download icons and DryRunBanner's check icon were moved
to Phosphor too, for one consistent family app-wide, not just in the
sidebar).

## Phase 3: Phosphor `fill` prop in ui_design.md's sample code doesn't exist
The design doc's `iconProps()` example passes both `color` and `fill` to
the icon component, implying independently-colorable duotone layers. The
actual `IconProps` type (`node_modules/@phosphor-icons/react/dist/lib/types.d.ts`)
only exposes `color` — duotone's second layer is a fixed-opacity variant
baked into the icon itself, not a separate prop. Implemented as: every icon
gets `color="currentColor"`, wrapped in a `.zamboni-nav-icon` span whose CSS
`color` is set inline per-item (see next entry) — hover/selected states
override to white via a CSS rule needing `!important` to beat that inline
style. This gets the intended "muted default -> white on hover/active"
behavior through pure CSS with no per-item JS hover tracking, matching
`ui_design.md`'s own suggestion that a CSS-driven approach is acceptable.

## Phase 3 (post-review, LOCKED — Sujith sign-off): sidebar categorized + colored per-category, not per-icon
`ui_design.md` explicitly said "no rainbow navigation icons" / "do not give
every icon a random different color" — a blanket per-icon rainbow. Sujith's
follow-up ask was for colored icons plus grouping the 13 routes into
sections; the two asks are reconciled as **one hue per category** (5
categories: Overview/icy-blue, Registry/teal, Monitoring/amber,
Governance & Safety/mint, Administration/violet — `ui/src/routes.tsx`'s
`ROUTE_CATEGORIES`), not 13 independent colors. This keeps the "no rainbow
per item" spirit of the original spec while giving the visual grouping and
color Sujith asked for. Mint for the Governance & Safety category
deliberately reuses the same hue the dry-run banner uses for
safety/reassurance surfaces, per `ui_design.md`'s own convention that mint
is reserved for that meaning.

## Phase 3 (post-review, LOCKED — Sujith sign-off): KPI row is a CSS grid, not an AntD 24-col Row/Col
Five KPI cards don't divide evenly into Ant Design's 24-column grid
(`span={24/5}` = 4.8) — this was rendering correctly in the math but the
row visibly stopped short of the page's full width in practice. Replaced
with a plain `display:grid; grid-template-columns:repeat(5,1fr)` (
`ui/src/pages/Home/home.css`), which also implements the 5→3→2→1 responsive
reflow `ui_design.md`'s RESPONSIVENESS section asks for and Phase 3's
initial build hadn't gotten to yet.

## Phase 3 (post-review, LOCKED — Sujith sign-off): Governance card fill is state-driven, not static
Original Phase 3 build gave the Home "Governance" card a static mint fill,
matching the dry-run banner's safety motif. That was wrong whenever
`conflicts.conflicted > 0`: the card read "calm/all-clear" via its green
background while a red "N conflicted table(s)" chip sat inside it, arguing
with its own container. Fixed as a 3-way, state-driven tone
(`ui/src/pages/Home/index.tsx`'s `governanceTone`):
- **Loading**: neutral white/`colorBorder` — state isn't known yet.
- **Clear** (`conflicted === 0 && activeLocks === 0`): the same mint
  gradient/border the dry-run banner uses — mint now consistently means
  "all clear," never a fixed decoration.
- **Needs attention** (either count > 0): soft amber wash
  (`#FFFAEB → #FFFDF6`, border `#FEDF89`, title `#8A5B12`) — same amber
  family as the warning status-tag tone in `colors.ts`, so it reads as one
  system rather than a one-off color.
**LOCKED as of 2026-07-06** — Sujith approved this after reviewing the
live amber-state render (1 conflicted table, 0 locks, in the seeded local
DB). Don't revert to a static fill without a new sign-off.

## Phase 3+: Health Dashboard built for real (was a placeholder), health_kpis extended
Sujith asked for a "major uplift" of the Health Dashboard with VP-level
charts: storage reclaimed trend, cost savings, healthy/unhealthy tables,
non-prod lifecycle, dry-run adoption. Built as a real
`ui/src/pages/HealthDashboard/` page (replacing its PlaceholderPage) rather
than piling more onto Home, since Home already covers the "VP glance" and
this is explicitly the deep-dive destination Home's Governance card already
links to. `api/services/executions_svc.py::health_kpis()` gained 7 new
fields (`reclaimed_storage_trend`, `top_tables_by_reclaim`, `cost_trend`,
`storage_savings`, `fleet_health`, `nonprod_funnel`, `dry_run_adoption`) —
kept on the one existing endpoint rather than a new route, since
contracts.md §6 already documents `GET /api/health/kpis` as serving both
Home and the Health Dashboard from one shared source.

- **"Storage Reclaimed" deliberately excludes compaction's bytes_rewritten.**
  Compaction rewrites files for query performance; it doesn't reduce total
  bytes. Only vacuum's `bytes_reclaimed` (orphan/snapshot removal) and
  archival's `bytes_archived` (moved to cheaper tier) count as genuinely
  freed capacity for the cost-savings framing.
- **Estimated $ savings, not live billing.** `S3_STANDARD_USD_PER_GB_MONTH`
  (`config/settings.py`, default $0.023) is a flat-rate illustrative
  estimate, same convention as `costs()`'s existing $5/TB Athena estimate —
  not a real AWS Cost Explorer figure. Computed all-time (not just the 30d
  trend window) since freed capacity stays freed going forward.
- **Fleet Health Scorecard is a proxy classification, not
  `engine/core/health_checker.py::check()`.** That function needs a live
  Iceberg `$snapshots`/`$files` call per table — fine for the Dry Run
  Viewer's single-table case, too expensive to run fleet-wide on every
  Health Dashboard load. Instead: AT_RISK = integrity failure in 7d, AWS
  optimizer conflict, or no successful run in 14d; NEEDS_ATTENTION = a
  failure in 7d otherwise; HEALTHY = neither. One aggregate SQL query, not
  N live per-table calls.
- **"Dry-Run Adoption" is a snapshot, not a trend line.** The schema has no
  "graduated_at" event to plot a real historical trend against
  (`stream_registry` only has current `dry_run_until`, not a change log).
  Built instead as "which domains have tables waiting longest in shadow
  mode" (`DATE_DIFF('day', registered_at, NOW())`), which answers Sujith's
  actual question — "which teams are hesitant" — more directly than a
  fabricated trend would have.
- **`UnhealthyTablesGrid` uses a plain AntD `<Table>`, not the shared
  `<DataGrid>`.** `fleet_health.tables` is a fixed array embedded in the
  KPI payload, not a server-paginated resource — DataGrid's
  query-result/pagination contract doesn't fit data shaped like this.
- **Found and fixed: `seed_local_db.py`'s dry-run ramp-up date had a sign
  bug.** `_date(-7 + 14)` evaluated to `_date(7)` (7 days *ago*), not
  "expires in 7 days" as the comment claimed, so `in_dry_run` and this new
  `dry_run_adoption` were silently always empty/zero in local mode. Fixed
  to `_date(-7)`. Also added `seed_vacuum_audit_demo_rows()` and
  `seed_archival_demo_rows()` — `vacuum_audit` had zero seeded rows (only
  ever populated by hand during Phase 1b's own manual verification) and
  `execution_log` had zero `engine='archival'` rows at all, so the new
  reclaim charts had nothing to show without them.

## Phase 3+ (post-review, LOCKED — Sujith sign-off on the final spacing): Sider made sticky; Snapshot Bloat Heatmap deferred to Health Dashboard; user/logout menu added
Three follow-ups from Sujith reviewing the Health Dashboard:

- **Sidebar was scrolling with page content — took two follow-up rounds to
  fully fix.** `App.tsx`'s `<Sider>` had no positioning, so on tall pages
  (the new Health Dashboard) it scrolled away with everything else. Round 1:
  `position: sticky; top: 0; height: 100vh` on the Sider, restructured into
  a flex column (brand, `flex:1 overflowY:auto` menu wrapper, user-menu
  footer pinned last) — but gave the *outer* Sider its own `overflow: auto`
  too, so both it and the inner menu wrapper could scroll. On Windows'
  classic (non-overlay) scrollbars this reserved real layout width from a
  219px-wide column, squeezing labels ("Domain Management" →
  "Domain Manage…"). Round 2: outer Sider changed to `overflow: hidden`
  (clip only, never its own scrollbar) so just the inner menu region can
  scroll. That alone wasn't enough — the nav list (13 items + 5 group
  labels) still slightly overflowed typical viewport heights, so the inner
  scrollbar still appeared and still cost width. Round 3: tightened spacing
  so the list fits without scrolling at all — `itemHeight: 34`,
  `itemMarginBlock: 2` plus tight group-title/brand/footer padding. Verified
  via Playwright (`scrollHeight <= clientHeight`) at 800/900/1000px — no
  overflow, no scrollbar, full labels. Round 4: Sujith called round 3 "too
  tight" (visually cramped, not a functional bug) — measured the nav list's
  *actual* natural height (`Element.scrollHeight` on `.ant-menu` itself,
  unconstrained) against available space at several viewport heights before
  guessing again, landing on `itemHeight: 36, itemMarginBlock: 3` +
  slightly more group-title/brand/footer padding (`sidebar.css`). This needs
  ~652px of nav-list height, which fits with slack from ~800px of available
  viewport height upward — comfortably covers normal windows, but a very
  short browser window (<~800px tall) could still show the inner scrollbar.
  Lesson: don't assume headless-Chromium overflow behavior matches a real
  Windows Chrome session (classic scrollbars there reserve width that
  overlay scrollbars in automated testing often don't) — and when tuning
  spacing, measure the unconstrained content height directly rather than
  just checking a binary "does it overflow" at one viewport size.
- **Snapshot Bloat Heatmap: confirmed not a must-have, deferred.** It's the
  one real gap in Fleet Health Scorecard (which deliberately proxies health
  from failures/integrity/conflicts, not live snapshot/small-file counts —
  see the Health Dashboard entry above) — but Sujith agreed it's a nice-to-
  have, not blocking. If built later, it belongs on the Health Dashboard
  (not Home) and should reuse `execution_log.snapshots_before` averaged by
  domain×tier rather than adding live per-table Iceberg calls.
- **User/logout menu added, but it's an honest stub.** There is no real
  session anywhere yet — `api/deps.py::get_current_user()` is still the
  env-var stub contracts.md D5 describes ("OIDC seam ... for SSO later"),
  not a login system to log out of. `api/services/system_svc.py::system_mode()`
  now also returns `user` (additive, same convention as the other endpoint
  extensions this phase). `ui/src/components/UserMenu.tsx` shows that
  username in a Sider-footer dropdown with a "Log out" item that pops a
  `message.info` explaining SSO isn't wired up yet — same honesty
  convention as `PlaceholderPage` for unbuilt routes, rather than faking a
  session or silently no-op'ing.
- **Home stays lean.** Discussed pulling a "tables need attention" chip and
  an "Est. Monthly Savings" stat onto Home's Governance strip, but nothing
  was implemented — Sujith said "all good for now." Revisit only if asked;
  don't duplicate Health Dashboard's charts onto Home in the meantime.

## Phase 1c: fleet_conflict_summary staleness computed in Python, not SQL INTERVAL HOUR
`engine/core/governance.py::fleet_conflict_summary()` fetches raw
`aws_opt_checked_at`/`gate0_override_until` values and computes
scanned/conflicted/stale_cache/overridden counts in Python (parsing
timestamps, comparing `age_hours`), rather than a single SQL query with
`NOW() - INTERVAL 'n' HOUR`. Reason: `engine/utils/local_db.py::_translate()`
only rewrites DAY-unit `INTERVAL` literals (`INTERVAL 'n' DAY` ->
bare `n`); an HOUR-unit clause reaches SQLite untranslated and is invalid
syntax there, which would fail the *entire* query (including the
conflicted/scanned counts that have nothing to do with staleness) in
`ZAMBONI_LOCAL_MODE`. This mirrors the convention
`conflict_detector.get_cached()` already established for the exact same
staleness check (parse timestamp, compute `age_hours` in Python) rather than
inventing a new one.
