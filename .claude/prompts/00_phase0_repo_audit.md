# PHASE 0 — Repo Audit & Baseline (read-only for app code)

You are auditing the Zamboni repo (Maestro IceForge Iceberg governance platform)
to re-baseline the AI memory files before a two-workstream replatform:
(A) engine hardening after a metadata-loss incident, (B) Streamlit → FastAPI+React.
~107 commits of improvements landed AFTER the memory files were written, so the
CODE is source of truth; the memory files are stale.

## Read first, in order
1. .claude/CLAUDE.md, .claude/decisions.md, .claude/components.md, .claude/context_hints.md
2. SHARED_CONTRACTS.md in the repo root (I placed it there) — this is the locked
   replatform contract you will commit as .claude/contracts.md.

## IMPORTANT — which repo this is
You are auditing the PERSONAL repo (public github lineage). It does NOT
contain the org-only components listed in the changelog below (CFN stack,
engine/operations/vacuum.py hardening, Parquet log writer, CloudTrail signals)
— that is EXPECTED, not an error. Verify what IS present; register what is
org-only. Contracts §10 governs how the build stays migration-safe.

## Known recent changes to verify against the code (from my changelog)
- Gate system fully wired: gate1/2/3_enabled in hk_config, read at runtime in
  engine/engines/hk_engine.py; templates carry gate flags; apply_template writes them.
- Control-M complete: controlm_pipeline_job / controlm_hk_job /
  dependent_on_controlm_job / dependent_job_type / controlm_job_start_time /
  controlm_expected_duration_min; controlm_jobs registry + ctrlm_helper.py;
  job-mapping CSV import/export.
- Table Registration rebuilt into 5 tabs with bulk Control-M sub-tabs.
- **NEW dirs/files not in memory**: engine/operations/vacuum.py (12 gap types,
  partition-type-aware), v2 features (idempotency, property sync, hot partition
  detection, backpressure, Parquet log writer for execution_log via
  EXECUTION_LOG_MODE), CloudTrail activity signals.
- **CI/CD exists**: deploy/zamboni-cfn.yaml + CodePipeline/CodeDeploy + buildspec.
- Branding: fixed 48px topbar, Zamboni logo, Inter font.
- Many session-state/SQL fixes (pd.NA, DATE_DIFF char-scan parser, _current_page).

## Tasks
1. **Walk the repo**: app/, engine/ (including engine/operations/ and any other
   new dirs), config/, scripts/, deploy/, tests/. Build a DELTA REPORT vs the
   four .claude files: new files, moved files, changed signatures, new columns,
   new env vars, new patterns. Pay special attention to:
   a. engine/operations/vacuum.py — summarize its public functions, the 12 gap
      types, how orphan deletion and snapshot expiry are actually invoked
      (Athena VACUUM? table properties? direct S3?), and where older_than /
      retention values come from.
   b. The execution_log writer — where the Parquet mode lives, its schema, and
      where new columns must be added for both modes.
   c. Backpressure/workgroup mapping — the function orchestration must route through.
   d. Whether ZAMBONI_MODE / get_mode() / get_boto3_session()-style session
      factory exists. Report YES/NO with file:line.
   e. deploy/ — list every CFN resource, the buildspec steps, CodeDeploy hooks.
2. **Regenerate all four .claude files** to match reality. Keep their structure;
   keep every "do not" in context_hints.md and ADD new ones you infer from
   recent fixes (e.g., the DATE_DIFF char-scan parser exists — note not to
   regress it).
3. **Commit the contract**: copy SHARED_CONTRACTS.md → .claude/contracts.md.
   Then RECONCILE it: where the contract references things that differ from
   reality (paths, existing columns, existing DDL location), annotate the
   contract inline with `> REALITY:` notes — do NOT change locked decisions.
4. **Baseline**: run `python -m pytest tests/unit/ -q` and `ruff check .`.
   Report the exact pass count (expected ≥494). Fix NOTHING in app code.
5. **Re-port backlog**: create `.claude/org_divergence.md` — a simple list of
   changelog items ABSENT from this repo (org-only: vacuum 12 gap types,
   Parquet log writer, CloudTrail signals, old CFN). One line each: what it
   is + why it may be worth re-porting org-side post-showcase (contracts §10
   R10.2). No adapters, no ledgers — the delivery is a clean drop.
6. **Conflict list for Sujith**: anything in my recent improvements that
   collides with the hardening plan (e.g., if vacuum.py already implements an
   orphan floor or its own locking) — list each with a recommended resolution.
7. Append to .claude/CLAUDE.md:
   `## Migration Progress` → `2026-07-05 Phase 0: baseline <N> tests, delta
   report done, contracts committed. Open questions: <list or none>.`

## Acceptance criteria
- Four regenerated .claude files + .claude/contracts.md committed-ready.
- Delta report covers 1a–1e explicitly with file:line references.
- pytest pass count reported; ruff clean; ZERO app-code modifications.
- Conflict list (possibly empty) presented for my sign-off.
- .claude/org_divergence.md (re-port backlog) created.

## Do NOT
- Modify any file under app/, engine/, config/, scripts/, deploy/, tests/.
- Renegotiate anything in contracts.md decisions D1–D6.
