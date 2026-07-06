# PHASE 5a — Wave 2 Part 1: Table Registration + Policy Configuration

Context: the two monster pages (~1,800 and ~1,200 Streamlit lines). Everything
hard about the Streamlit versions — sticky checkboxes, forms-that-don't-react,
flash gymnastics — dissolves in React; do NOT port the workarounds, port the
FEATURES. Pattern remains Home/PATTERN.md; these pages may add local
sub-components but not new global patterns.

## Read first
ui/PATTERN.md, .claude/contracts.md §6 (tables, policies, gates, controlm
routers), app/pages/2_Table_Registration.py and 3_Policy_Configuration.py
FULLY (feature inventory — read in chunks, list every tab/field/validation/
action before writing code), api/services for the lifted logic (import-CSV
matching, template apply, gates PUT).

## Page 1: pages/TableRegistration/ — Tabs (AntD Tabs) mirroring the 5-tab layout

**Tab 1 Browse & Discover** — GET /api/glue/databases → Select; GET
/api/glue/tables/{db}?pattern&unregistered_only. DataGrid with native
rowSelection (checkbox) — Select All/Clear are free via AntD selection; pattern
Input.Search; "unregistered only" Switch default OFF (show-all — locked by the
domain-move use case). Selected rows → Register drawer/section: Domain/Layer/
Tier Selects with placeholder "— select —" + required validation, owner/CI,
engine flags, Control-M block (see shared component below). POST
/api/tables/register per row or a bulk variant if the service exposes it —
sequential with progress + per-row result list.

**Tab 2 Registered Tables** — GET /api/tables full filters; ALL columns incl.
HK/Archive/Lifecycle badges, Control-M cols, gates; CSV export.

**Tab 3 Edit Table** — table search Select → form (Form antd, initialValues
from GET /api/tables/{fqn}); Control-M block; PUT on submit; audit toast.

**Tab 4 Bulk Control-M** — sub-tabs: Manual Apply (filter fields in the LOCKED
row order from the Streamlit version: Job Name/start/duration → HK/Gate1/Type →
CI/Stream; preview → POST /api/tables/bulk-controlm with match count confirm),
Import Mapping (CsvButtons upload → per-row match report grid → apply),
Export Template (download), Job Registry (GET/POST /api/jobs grid + add form +
CSV import).

**Shared: components/ControlMFields.tsx** — the 6-field block with job-name
AutoComplete fed by GET /api/jobs?search (the ctrlm_helper equivalent: options
from registry + free text allowed), Job Type Select
(controlm/glue/lambda/step_functions/airflow/other), TimePicker HH:mm, duration
InputNumber. Used by Register, Edit, Bulk tabs.

## Page 2: pages/PolicyConfig/ — Tabs: View | Edit Table | Bulk Apply | Templates

**View** — GET /api/policies grid: template, strategy, engine, snap days,
frequency, Gate0-override indicator, Gate1/2/3 ✅❌ (StateBadge), filters.

**Edit Table** — search Select → GET /api/gates/{fqn} + /api/policies/{fqn}.
Sections: (a) **Gates card**: Gate 1/2/3 Switches + Gate 0 override control —
DatePicker (max now+GATE0_OVERRIDE_MAX_HOURS, disable beyond) + required reason
Input when set + current override status Alert; (b) Window & blackout: window
type Select (react instantly — this is just React state now), conditional
start-time, delay/duration, timezone, blackout presets Select + 24 Checkbox
grid (preset populates state); (c) compaction/snapshot numbers with the same
validations the Streamlit form enforced (min/max, strategy×engine cross-check —
replicate the exact rules found in your feature inventory). Submit → PUT
/api/policies/{fqn} + PUT /api/gates/{fqn}; both audited toasts.

**Bulk Apply** — template Select + domain/layer/tier filters → affected-count
preview (the apply endpoint should support a dry_run preview returning count —
it does via dry_run=true) → confirm modal → apply.

**Templates** — list; edit drawer with SAME gates+window controls (shared
sub-components with Edit Table — extract components/GatesEditor.tsx and
WindowBlackoutEditor.tsx used by both); PUT /api/templates/{name}.

## Acceptance criteria
- Feature-inventory checklists for BOTH Streamlit pages produced first, then
  checked off — output both checklists complete.
- tsc clean; build succeeds; pytest unit+api green.
- Round-trips proven in dev mode (describe): register a Glue-discovered table →
  appears in Registered grid; edit gates on a table → View tab reflects ✅❌;
  set a 4h Gate 0 override with reason → /api/gates shows it, audit toast id;
  job-mapping CSV import 2-row file → match report → apply → fields visible in
  Registered grid; template edit gate flag → bulk apply → policies grid updated.
- Validation parity: submitting register with placeholder Domain blocks with
  message; override without reason blocks.
- Migration Progress appended.

## Do NOT
- Port Streamlit workarounds (_fk key prefixes, flash two-step, filter-hash
  editor keys) — they are obsolete here.
- Skip any tab/sub-tab from the inventory.
- Create global patterns; local sub-components under each page only, except
  ControlMFields/GatesEditor/WindowBlackoutEditor which live in ui/src/components/.

Suggested commit: `feat(ui): wave 2a — Table Registration (5 tabs) + Policy Configuration (gates/window/templates)`
