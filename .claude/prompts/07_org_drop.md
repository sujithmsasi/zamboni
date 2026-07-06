# PHASE 7 — Org Drop & Deploy (run on ORG laptop, Bedrock Sonnet 5 / 4.6)
# Clean-drop delivery per contracts §10 — NO merging with the old org branch.

You are delivering a finished, tested codebase into this org repo as a NEW
branch. The old branch is retired from active development; do not merge,
diff-reconcile, or port anything from it in this session.

## Inputs (transferred: the personal repo's final state)
The complete new codebase (zip or clone of the public repo, dev branch,
post-Phase-6). It contains: hardened engine (locks, Gate 0, orchestrator,
integrity, recovery), FastAPI api/, React ui/, complete deploy/ stack
(zamboni-cfn.yaml, buildspec, appspec, hooks, systemd units),
docs/ (runbooks, showcase runbook, ORG_DROP.md), .claude/ memory files.

## Tasks
1. Branch: `git checkout --orphan new-phase1` (or `enhancement`) in the org
   repo → remove tracked files → drop the new codebase in → single commit
   `feat: Zamboni replatform — hardened engine + FastAPI/React enterprise UI`
   → push. (Orphan branch keeps org history clean; plain new branch is also
   acceptable — Sujith's call at runtime.)
2. Adapt deploy/zamboni-cfn.yaml parameters to org conventions ONLY:
   NamePrefix, tags, VpcId/SubnetId, SourceCidr, bucket names, SNS ARNs,
   GitHub source. Do not restructure resources. Run cfn-lint.
3. Create/verify prerequisites: `python scripts/create_lock_table.py`
   (or let CFN create it), Athena workgroup, Glue database + metadata table
   DDLs (scripts/ddl/), .env from .env.aws_local.example / EC2 profile.
4. Deploy: CFN stack (or reuse an existing dev EC2 by installing per
   docs/deployment/), then CodeDeploy pipeline if org process requires it.
5. Acceptance on the org clone:
   python -m pytest tests/ -q → zero failures; ruff check . → clean;
   cd ui && npm ci && npm run build → clean;
   python scripts/aws_smoke_test.py (aws_local or on-instance) → paste table;
   orchestrator dry-run on ONE low-risk registered table → paste RunResult;
   open the app (:8000) → Home + Health/Governance render real fleet data.
6. Post-showcase backlog (record as issues, do NOT do now): re-port from the
   old branch — vacuum 12-gap-type hardening into engine/core/maintenance_ops
   bindings, Parquet log writer mode, CloudTrail activity signals
   (list source: .claude/org_divergence.md).

## Rules
- Contracts semantics are LOCKED (orphan floor 72h clamp-up, snapshot min-age,
  Gate 0 order, orchestrator sequence). No weakening during adaptation.
- Anything ambiguous about org conventions: stop and ask Sujith.
