# PHASE 3 — React Foundation + Home (the canonical pattern)

Context: FastAPI layer is live (Phase 2). This phase creates the React app
shell — white enterprise Ant Design theme — and builds the Home page COMPLETELY.
Home becomes the canonical pattern that Waves 1–2 replicate verbatim. Quality
here compounds; sloppiness here multiplies across 12 pages.

## Read first
.claude/contracts.md §6 (endpoints Home consumes: /api/health/kpis,
/api/system/mode, /api/conflicts summary via /api/health/kpis or governance),
§7 (structure + theme — LOCKED), .claude/CLAUDE.md, api/models.py (shapes to
mirror in types.ts), app/Home.py + app/pages/4_Health_Dashboard.py (what the
current Home shows — KPI names, sections — parity target).

## Tasks

### 1. Scaffold ui/ exactly per contracts §7
Vite react-ts template; deps: react-router-dom, @tanstack/react-query, antd,
@ant-design/icons, dayjs. devDeps: typescript, @types/*. vite.config.ts:
server.proxy { '/api': 'http://localhost:8000' }; build.outDir 'dist'.
tsconfig strict. Add Inter font (index.html link or @fontsource/inter).

### 2. theme.ts — the LOCKED tokens from contracts §7 verbatim
(Sujith will tune values during acceptance; structure/keys stay.)

### 3. App shell
- main.tsx: QueryClientProvider (staleTime 30_000 default, retry 1) →
  ConfigProvider(zamboniTheme) → BrowserRouter → App.
- App.tsx: AntD Layout — Sider (light, logo top: reuse the transparent
  zamboni-logo.png — copy into ui/public/), Menu with ALL 13 routes (pages not
  yet built render a PlaceholderPage saying "migrating — use Streamlit :8501"),
  Header (48px, page title via route meta, right side: env Tag from
  /api/system/mode + DryRunBanner trigger), Content (padding 16, maxWidth none).
- DryRunBanner.tsx: if system mode dry_run_default → sticky Alert banner
  "DRY RUN mode — no writes will be executed".

### 4. API client layer
- api/client.ts: `request<T>(path, init?)` → fetch, unwrap envelope, throw
  ApiError{code,message} on error field or !ok; JSON + multipart helpers;
  qs builder for pagination/filters.
- api/types.ts: envelope, Pagination, MutationResult, KpiSet, SystemMode,
  TableRow, ConflictRow (extend per need — mirror Pydantic names snake_case;
  keep snake_case in TS to avoid mapping bugs — LOCK this convention, note in
  contracts §7 via a `> REALITY:` addition).
- api/hooks/useSystem.ts, useHealth.ts: useQuery wrappers with typed keys
  ['system','mode'], ['health','kpis'].

### 5. Shared components (contracts §7 list)
- DataGrid.tsx: props {columns, queryResult, rowKey, rowSelection?, onPageChange,
  toolbar?}. AntD Table: pagination {pageSizeOptions:[15,25,50,100,250],
  showSizeChanger, showTotal}, loading from query, size="small", sticky header,
  scroll.x auto. Server-side: onChange lifts page/size/sorter to caller.
- PageHeader.tsx, StateBadge.tsx (map lifecycle/gate/status → Tag colors:
  ACTIVE green, STALE_CANDIDATE gold, GREENZONE orange, PENDING_DROP red,
  DROPPED default, VERIFIED green, FAILED red), CsvButtons.tsx (Upload accept
  .csv → POST multipart; export → window.location to export URL).

### 6. Home page — COMPLETE (pages/Home/)
Parity with current Streamlit Home + the new governance summary:
- KPI cards row (AntD Statistic in Card grid): registered tables, HK-enabled,
  coverage %, executions today, failures 7d — from /api/health/kpis.
- Governance strip: conflicted-tables count + "view report" link →
  /health route (Wave 1 will host the full report) + active locks count
  (/api/locks length).
- Recent activity mini-grid: last 10 executions (reuse /api/executions?size=10)
  via DataGrid (no selection).
- All states handled: Skeleton while loading, Alert+retry on error, Empty hints.
- Home/hooks.ts holds every useQuery — index.tsx is layout only. THIS SPLIT IS
  THE PATTERN.

### 7. Pattern documentation
Create ui/PATTERN.md (≤60 lines): the Home anatomy — folder shape, hooks file,
query keys convention ['domain','resource',params], DataGrid usage, mutation
recipe (useMutation → invalidateQueries → message.success(audit_id)), error/
loading/empty handling. Waves 1–2 prompts point here.

## Acceptance criteria
- `cd ui && npm run build` → success, no TS errors (`tsc --noEmit` clean).
- Dev proof: with uvicorn on :8000 (local mode) + `npm run dev`, Home shows
  LIVE seeded numbers (list the KPI values you see).
- Prod-serve proof: after build, uvicorn alone on :8000 serves the app at /
  (Phase 2 static mount) — confirm Home loads.
- White theme visible: white cards on #f5f7fa canvas, navy primary, Inter.
  Screenshot-describe the Home layout for my sign-off — I will tune tokens in
  this session if needed (allow one adjustment round on theme.ts only).
- ui/PATTERN.md written. pytest/ruff still green (no Python changes expected;
  verify anyway). Migration Progress appended.

## Do NOT
- Add Redux/Zustand/axios/styled-components/Tailwind. AntD + fetch + Query only.
- Build any other page beyond Home + placeholders.
- Deviate from contracts §7 file layout.

Suggested commit: `feat(ui): React foundation — AntD white theme, shell, API client, canonical Home`
