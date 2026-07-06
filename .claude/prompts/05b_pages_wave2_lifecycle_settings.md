# PHASE 5b — Wave 2 Part 2: NonProd Lifecycle, Stale Resources, Settings

Context: final three pages. After this, all 13 routes are live and Streamlit
becomes fallback-only. Pattern discipline unchanged (ui/PATTERN.md).

## Read first
ui/PATTERN.md, .claude/contracts.md §6 (lifecycle, executions/stale, settings
routers), app/pages/9_NonProd_Lifecycle.py, 10_Stale_Resources.py,
11_Settings.py, 12_Audit_Log.py check (audit already done Wave 1 — confirm),
engine lifecycle constants (thresholds for the explainer).

## Page 1: pages/NonProdLifecycle/ — Tabs mirroring the 4-tab layout
1. **State Overview** — GET /api/nonprod?env&state; the "How are stale tables
   identified?" explainer as a Collapse panel — thresholds rendered from a
   small GET (add fields to /api/system/mode or a /api/lifecycle/config in the
   lifecycle service if constants aren't exposed; `> ADDED` note in contracts
   if you add it) so numbers never drift from engine constants.
2. **Bulk Exemption / Claim** — grid of actionable states
   (STALE_CANDIDATE/GREENZONE/PENDING_DROP, priority-sorted PENDING_DROP first)
   with rowSelection; shared reason TextArea (required); two buttons →
   POST /api/nonprod/exempt | /claim with fqns+reason; per-row results;
   invalidate lifecycle keys.
3. **Single Table Action** — Select showSearch over nonprod tables (state +
   days-inactive shown in option/description), same exempt/claim actions.
4. **Deletion History** — GET /api/nonprod/deletions grid.

## Page 2: pages/StaleResources/ — Tabs: Stale HK | S3 Orphans | Zero-Row | NonProd Stale
Each backed by GET /api/stale?kind=...&filters (threshold days InputNumber where
the Streamlit twin had it, domain/env filters). KPI strip per tab (counts —
never-housekept, hk-disabled etc. per the twin). CSV export per grid. pd.NA-class
display bugs are gone by construction — badges via StateBadge with null-safe
formatting in the service layer (verify services already null-safe; fix there
if not, with an api test).

## Page 3: pages/Settings/ — Tabs: General | Enforcement | Escalation Matrix | Advanced
- General/Enforcement/Advanced: GET/PUT /api/settings forms mirroring the twin's
  fields.
- **Escalation Matrix**: grid of entries (lookup-key + 4 fields); Add/Edit in a
  Drawer (key disabled on edit — PK), Delete with confirm; POST/PUT/DELETE
  /api/escalation; the lookup-precedence explainer text preserved. Instant grid
  refresh via invalidation (the old navigate-away bug is structurally gone —
  note that in the parity checklist).
- Active locks admin view can also live here (link to LiveActivity's strip or
  duplicate the small component) — your call, note the choice.

## Cross-cutting closure tasks
- Remove all PlaceholderPage usages; Menu final.
- ui/PATTERN.md: append a short "page inventory" table (route → endpoints used).
- Streamlit pages remain untouched and running (:8501) — add ONE line banner
  component to Streamlit header? NO — do not touch Streamlit this phase.
- Sweep: grep ui/src for `any`, TODO, console.log — clean or justify.

## Acceptance criteria
- Feature-inventory checklists for the three twins produced and checked off.
- tsc clean; build succeeds; pytest unit+api green (incl. any added endpoints'
  tests).
- Dev-mode proofs (describe): bulk-exempt 2 seeded GREENZONE tables with reason
  → states flip to ACTIVE in grid + audit toasts; escalation entry add→edit→
  delete round-trip with instant grid refresh; each Stale tab renders seeded
  data with correct KPI counts.
- All 13 menu routes render real pages; no placeholders remain.
- Migration Progress appended: "UI feature-complete; Streamlit = fallback".

Suggested commit: `feat(ui): wave 2b — nonprod lifecycle, stale resources, settings/escalation; UI complete`
