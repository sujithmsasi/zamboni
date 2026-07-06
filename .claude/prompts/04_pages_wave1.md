# PHASE 4 — Pages Wave 1 (7 read-heavy pages, Home pattern replication)

Context: Foundation + Home are done and approved. This wave stamps out the
seven read-heavy pages. RULE ZERO: replicate the Home pattern EXACTLY —
same folder anatomy, hooks split, DataGrid, query-key convention, states.
Inventing a new pattern here is a defect even if it "works".

## Read first
ui/PATTERN.md, ui/src/pages/Home/* (the template), .claude/contracts.md §6
(endpoints per page below), and for EACH page its Streamlit twin in app/pages/
(the parity checklist — every filter, column, badge, export that exists there
must exist here, minus Streamlit-specific workarounds).

## Pages, endpoints, and parity notes

1. **HealthDashboard/** — /api/health/kpis, /api/conflicts, /api/executions
   (integrity_status=FAILED, 7d), POST /api/conflicts/rescan.
   Includes the FULL Dual-Optimizer Risk Report grid (filters: domain; CSV
   export via ?export=csv), rescan button (mutation → invalidate ['conflicts']),
   integrity-failures grid. This page REPLACES the 1c Streamlit stopgap as the
   governance showcase surface — make it presentation-clean.
2. **LiveActivity/** — /api/executions?status=RUNNING + recent, auto-refresh:
   useQuery refetchInterval 10_000 on this page only. Active locks strip
   (/api/locks) with force-release action (DELETE, confirm modal, admin-feel,
   audited toast).
3. **ExecutionLog/** — /api/executions full filters (fqn search, engine, status,
   date range via DatePicker.RangePicker), row expand → /api/executions/{id}
   detail incl. metadata before/after + integrity badge (StateBadge).
4. **CostReport/** — /api/costs?group_by=domain|layer|tier + range; AntD
   Statistic summary + DataGrid; simple Column chart optional ONLY if
   @ant-design/plots is added — prefer skipping charts this wave (note in
   PATTERN.md if added).
5. **AuditLog/** — /api/audit filters (actor, action, range), expandable
   before/after values (pre-formatted JSON in expanded row).
6. **DryRunViewer/** — table search (Select showSearch fed by /api/tables?search=),
   then /api/dryrun/{fqn}: gate summary (Gate 0 conflict/override state, Gates
   1–3 enabled + would-pass) as a Descriptions/List with StateBadge, planned
   operations list. Parity with the Streamlit gate summary EXACTLY (it shows
   real hk_config flags).
7. **DomainManagement/** — /api/... (contracts has no explicit domains router:
   REALITY check — Phase 2 should have exposed domains via settings or a
   tables/domains endpoint; if Phase 2 skipped it, ADD GET/POST/PUT
   /api/domains to settings_router + service now, matching the Streamlit
   page's fields, update contracts.md with a `> ADDED (Phase 4):` note, and
   add API tests). CRUD with modal form, escalation-routing preview read-only.

## Cross-cutting tasks
- Replace each route's PlaceholderPage; Menu items now live.
- Every grid: server pagination through DataGrid; column sets mirror the
  Streamlit twin (strip glue_catalog. in display).
- Mutations (rescan, force-release, domain CRUD): confirm modal on destructive,
  dry_run from global context where applicable, success toast with audit_id,
  invalidate correct keys.
- types.ts additions per response; NO any.

## Acceptance criteria
- `tsc --noEmit` clean; `npm run build` succeeds.
- pytest (unit+api) green — if Domain endpoints were added, new api tests included.
- Side-by-side parity check: for EACH page, list its Streamlit twin's features
  and check them off (filters/columns/actions) — output this checklist.
- Live proof in dev mode: describe each page rendering seeded data; Health
  shows ≥1 conflicted table; LiveActivity auto-refresh interval confirmed.
- No pattern deviations (or each justified in decisions.md — target: zero).
- Migration Progress appended with per-page status.

Suggested commit: `feat(ui): wave 1 — health/governance, activity, logs, costs, audit, dry-run, domains`
