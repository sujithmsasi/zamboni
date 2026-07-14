# Zamboni Sonnet 5 Prompt Pack — README

One phase = one fresh Sonnet 5 session on claude.ai. Venue: PERSONAL repo.
Delivery = CLEAN DROP to a new org branch (`new-phase1`) — no merging with
the old org branch (contracts §10). Phase 7 (the drop + CFN adapt + deploy)
runs on the org laptop via Bedrock Sonnet.

## Workflow per phase (claude.ai session loop)
1. Fresh zip of your current personal repo:
   `Compress-Archive -Path zamboni -DestinationPath zamboni-current.zip -Force`
2. New chat, model = **Claude Sonnet 5**. Attach: zamboni-current.zip + the
   phase .md. Paste the kickoff block from PHASE_EXECUTION_PROMPTS.md.
3. Let it run (extracts zip → reads .claude/ → executes → repackages).
4. Download the produced zip + copy script → sync to your repo → YOU run the
   acceptance commands locally.
   - Pass → commit (suggested message) → push to public GitHub → next phase.
   - Fail → reply in the SAME session with exact error output. One retry.
   - Fails twice → escalate to **Fable 5** (escalation package template).
5. Phase 0 commits .claude/contracts.md + docs/ORG_DROP.md + the re-port
   backlog (.claude/org_divergence.md). Contract changes: edit contracts.md
   FIRST, commit, then run the phase.
6. Delivery: ONE clean drop after Phase 6 → org new branch via Phase 7
   (protocol: ORG_DROP.md). Optional early drop after 1c if you want the
   incident fix in org sooner — same protocol, same branch, second commit.

## Phase order & timeline (today = Jul 5 → showcase Jul 17)

| Date    | Phase(s)                          | Outcome |
|---------|-----------------------------------|---------|
| Jul 5   | 0 (audit) + 1a (safety core)      | Baseline true, locks + Gate 0 live |
| Jul 6   | 1b (orchestrator) + 1c (recovery) | Incident fix complete, rollback tool, conflict report |
| Jul 7   | 2 (FastAPI layer)                 | Full API against engine, OpenAPI docs |
| Jul 8   | 3 (React foundation + Home)       | White theme approved, canonical pattern set |
| Jul 9–10| 4 (Wave 1: 7 read-heavy pages)    | Health/Activity/Logs/Costs/Audit/DryRun/Domains |
| Jul 11  | 5a (Table Registration + Policy)  | The two monsters |
| Jul 12  | 5b (Lifecycle + Stale + Settings) | UI complete |
| Jul 12  | 6 (full CFN + demo scripts)       | Complete standalone deploy stack |
| Jul 13  | **Phase 7 — org drop (org laptop)** | new-phase1 branch, CFN adapted, deployed |
| Jul 14–15| Real-AWS hardening (escalation window) | Break it, fix it — aws_local |
| Jul 16  | Demo polish + 2 rehearsals        | Click-path locked |
| Jul 17  | **Showcase**                      | 🎤 |
| TBD (post-showcase, opt-in) | 8a (Glue Job State Change triggers) | Targeted HK dispatch shortly after a mapped upstream Glue job SUCCEEDED, hourly rule untouched |
| TBD (post-showcase, opt-in) | 8b (Glue Data Catalog event triggers) | Narrowed discovery/HK scans from Database/Table/Partition catalog events, no auto-register/delete |
| TBD (post-showcase, opt-in) | 8c (Partition-targeted compaction) | Upgrades 8b's partition events into an event-verified `OPTIMIZE ... WHERE` predicate instead of `compaction.py`'s existing date-lookback heuristic; VACUUM stays table-level, unaffected |

Phase 8 (a/b/c) is proposed, not scheduled against the Jul 17 showcase —
all three are opt-in, default-`DISABLED` enhancements layered on top of the
already-complete hourly EventBridge reconciliation rule (see the
2026-07-09 "EventBridge engine scheduling" entry in `.claude/CLAUDE.md`).
Run 8a and 8b independently or in either order; they share the SQS/consumer
*pattern* but must not share a queue or rule. 8c is a strict follow-on to
8b — it changes what 8b's catalog-event consumer does with partition
events, it does not add a new event source, and its idempotency-key fix
(task 4 in its own doc) is a blocking prerequisite before enabling it. See
`08a_glue_job_event_triggers.md` / `08b_glue_catalog_event_triggers.md` /
`08c_partition_targeted_compaction.md`.

Engine phases (0–1c) land first ON PURPOSE: worst case, you still demo the
incident fix + Dual-Optimizer Risk Report from the Streamlit stopgap panel.

## Ground rules baked into every prompt
- Read `.claude/CLAUDE.md`, `contracts.md`, `context_hints.md` before writing.
- Contracts are locked. No renegotiation mid-phase.
- Targeted string edits on existing large files; full writes only for new files.
- `ast.parse` / `tsc --noEmit` after every edit. pytest + ruff before finishing.
- Extend `engine/operations/vacuum.py` and v2 features — never replace.
- Append a dated `## Migration Progress` entry to CLAUDE.md at the end.

## Files in this pack
```
SHARED_CONTRACTS.md                     → becomes .claude/contracts.md (Phase 0 commits it)
00_phase0_repo_audit.md
01a_safety_core.md
01b_orchestrator_integrity.md
01c_recovery_governance.md
02_fastapi_layer.md
03_react_foundation.md
04_pages_wave1.md
05a_pages_wave2_registration_policy.md
05b_pages_wave2_lifecycle_settings.md
06_deploy_cutover.md
07_org_drop.md                          → run on ORG laptop (Bedrock) — the clean drop
08a_glue_job_event_triggers.md          → proposed, opt-in — Glue Job State Change → targeted HK
08b_glue_catalog_event_triggers.md      → proposed, opt-in — Glue Catalog events → discovery/HK
08c_partition_targeted_compaction.md    → proposed, opt-in — follow-on to 8b, event-derived OPTIMIZE predicate
ORG_DROP.md                             → the delivery protocol (Phase 0 commits it)
PHASE_EXECUTION_PROMPTS.md              → per-session kickoff/retry/escalation blocks
```

## Before you start (5 minutes)
1. Commit your personal repo's current state to public GitHub `dev`.
2. Copy `SHARED_CONTRACTS.md` and `ORG_DROP.md` into the repo root
   (Phase 0 relocates/commits them).
3. Note which laptop `aws sso login --profile prod-toolsgenai-sso` works on —
   that machine is the aws_local demo machine (likely org → Phase 7 must
   finish before rehearsals).
