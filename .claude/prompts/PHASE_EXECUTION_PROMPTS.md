# Zamboni Phase Execution — Session Kickoff Prompts
# Copy the block for your current phase into a FRESH Sonnet 5 chat.
# Attach the referenced file(s) OR paste their contents below the block.

═══════════════════════════════════════════════════════════════════
UNIVERSAL TEMPLATE (works for any phase — swap the phase line)
═══════════════════════════════════════════════════════════════════

You are executing ONE phase of the Zamboni replatform program.

Repo: attached as a zip (my PERSONAL repo — contracts §10 applies; the org
repo's extra components are intentionally absent). First action: extract the
zip to /home/claude/zamboni and work there. All prior phases are committed
inside it; .claude/CLAUDE.md "## Migration Progress" tells you exactly what
has shipped — trust it and the code over any assumption.

EXECUTE: Phase <PHASE_ID> — as specified in the attached
<PHASE_FILE>.md. That file is your complete work order: its Read-first list,
Tasks, Acceptance criteria, and Do-NOT list are binding.

Rules for this session:
1. SCOPE: Phase <PHASE_ID> only. Do not start, preview, or "prepare" any
   other phase, even if you finish early. Other phase files (if attached)
   are context only — do not execute them.
2. CONTRACTS: .claude/contracts.md is LOCKED. If a task conflicts with it or
   with repo reality, STOP and ask me — do not improvise a resolution.
3. READ BEFORE WRITE: complete the phase file's Read-first list before your
   first edit. Cite file:line when you reference existing code.
4. EDIT DISCIPLINE: targeted string replacements on existing files; full
   writes only for NEW files. Verify syntax after every edit (ast.parse for
   .py; tsc --noEmit for .ts/.tsx when ui/ exists).
5. VERIFY: before declaring done, run the phase's acceptance commands and
   paste the ACTUAL raw output (pytest tail with pass count, ruff result,
   build output where applicable). No summaries in place of output.
6. HANDOFF: append the dated "## Migration Progress" entry to
   .claude/CLAUDE.md, then REPACKAGE: produce the updated repo zip in
   /mnt/user-data/outputs (exclude __pycache__/.pytest_cache/*.db/node_modules;
   include ui/ sources but not ui/dist) plus the copy_zamboni.ps1 sync script,
   and end with the suggested commit message.
7. If anything in acceptance FAILS: fix it within this session; do not hand
   me a red build. If you cannot fix after a genuine attempt, stop and give
   me: the failing command, full output, your diagnosis, files touched so far.

Begin with: (a) a 5-line plan of the tasks in order, (b) then start the
Read-first list. Ask zero clarifying questions unless rule 2 triggers.

[ATTACH: zamboni-current.zip + <PHASE_FILE>.md — plus SHARED_CONTRACTS.md and
ORG_DROP.md ONLY for Phase 0]

═══════════════════════════════════════════════════════════════════
PHASE-SPECIFIC ONE-LINERS (paste into the EXECUTE line above)
═══════════════════════════════════════════════════════════════════

Phase 0 :  EXECUTE: Phase 0 — Repo Audit & Baseline — per attached
           00_phase0_repo_audit.md. Also attached: SHARED_CONTRACTS.md and
           ORG_DROP.md (commit as .claude/contracts.md and docs/ORG_DROP.md).
           REMINDER: this phase is read-only for app code.

Phase 1a:  EXECUTE: Phase 1a — Safety Core (lock service, conflict detector,
           Gate 0) — per attached 01a_safety_core.md.

Phase 1b:  EXECUTE: Phase 1b — Orchestrator, Integrity Checker, Safe Vacuum —
           per attached 01b_orchestrator_integrity.md.
           REMINDER: extend engine/operations/vacuum.py — never replace.

Phase 1c:  EXECUTE: Phase 1c — Recovery Tooling & Governance Report — per
           attached 01c_recovery_governance.md.

Phase 2 :  EXECUTE: Phase 2 — FastAPI Layer (full locked contract §6) — per
           attached 02_fastapi_layer.md.

Phase 3 :  EXECUTE: Phase 3 — React Foundation + canonical Home — per
           attached 03_react_foundation.md.
           REMINDER: pause for my theme sign-off before finishing (one
           adjustment round on theme.ts allowed in-session).

Phase 4 :  EXECUTE: Phase 4 — Pages Wave 1 (7 read-heavy pages) — per
           attached 04_pages_wave1.md.
           REMINDER: replicate ui/PATTERN.md exactly; produce the per-page
           parity checklists.

Phase 5a:  EXECUTE: Phase 5a — Table Registration + Policy Configuration —
           per attached 05a_pages_wave2_registration_policy.md.
           REMINDER: feature-inventory both Streamlit twins BEFORE coding.

Phase 5b:  EXECUTE: Phase 5b — NonProd Lifecycle, Stale Resources, Settings —
           per attached 05b_pages_wave2_lifecycle_settings.md.

Phase 6 :  EXECUTE: Phase 6 — Full CFN + Demo Scripts + Drop Prep — per
           attached 06_deploy_cutover.md.
           REMINDER: the CFN is authored FRESH and standalone (contracts
           §10 R10.3) — parameterized for org adaptation; cfn-lint it.

═══════════════════════════════════════════════════════════════════
RETRY PROMPT (same chat, after acceptance failed on YOUR machine)
═══════════════════════════════════════════════════════════════════

Acceptance failed on my machine. Fix within THIS phase's scope only.

Command run:
<command>

Full output:
<paste raw output>

Constraints unchanged: contracts locked, targeted edits, re-run the full
acceptance suite after your fix and paste raw output. If your fix would
require touching files outside this phase's scope, stop and tell me why
instead of doing it.

═══════════════════════════════════════════════════════════════════
ESCALATION PACKAGE (bring to Fable 5 after a second failure)
═══════════════════════════════════════════════════════════════════

Phase <PHASE_ID> failed acceptance twice with Sonnet 5. Take over this phase.

1. Phase work order: <attach the phase .md>
2. Diff so far:      <paste `git diff` or attach patch>
3. Failing command + full raw output: <paste>
4. Sonnet's diagnosis from the retry: <paste its last explanation>
5. Anything I changed manually in between: <list or "nothing">

Same rules apply: contracts locked, this phase's scope only, raw acceptance
output before done.

Phase 7 :  (Runs on the ORG laptop with Bedrock Sonnet 5/4.6, NOT here.)
           Paste 07_org_drop.md into Claude Code / Bedrock chat on the org
           machine with the transferred codebase available. Clean drop —
           no merging with the old branch.
