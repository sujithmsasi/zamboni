# Zamboni — Org Divergence / Re-port Backlog

Per contracts.md §10 R10.2: features that exist on the org-only branch but
NOT in this personal repo. Not rebuilt here — candidates for a post-showcase
re-port done org-side (Bedrock Claude reading both branches). One line each,
no adapters, no ledgers.

- **Vacuum 12-gap-type, partition-type-aware hardening** — the org branch's
  `engine/operations/vacuum.py` reportedly implements 12 distinct gap fixes
  with partition-type awareness. This repo's version has 5 named gaps
  (1,2,3,9,10) and no partition-type branching — confirmed via direct read,
  Phase 0. Worth re-porting because it's likely the more mature/robust
  vacuum implementation, and per the Conflict List in contracts.md it may
  also resolve the orchestrator's orphan-delete mechanism mismatch (a
  parameterized orphan-only call, if the org version has one) — worth
  checking whether this backlog item should actually move INTO Workstream A
  rather than wait post-showcase.
- **Parquet log writer for execution_log via EXECUTION_LOG_MODE** — the
  changelog lists this as org-only, but it is in fact ALREADY PRESENT in
  this repo at `engine/core/execution_log_parquet.py` (ParquetLogBuffer,
  modes parquet/insert/both/auto). Recorded here only so a future re-port
  pass doesn't waste time trying to port something that already exists —
  no action needed; this line exists to correct the changelog, not to queue
  work.
- **CloudTrail activity signals** — like the Parquet log writer above, this
  is ALSO already present in this repo, contradicting the changelog.
  `engine/monitoring/activity_scanner.py` has a working `_query_cloudtrail()`
  (lines 109+) queried whenever `CLOUDTRAIL_TABLE` is set, with fallback to
  Glue `CreateTime` only when it's empty or the query fails. Recorded here
  only to correct the changelog — no re-port action needed unless the org
  version has additional signal types this one lacks (not checked in this
  pass; would need a side-by-side diff against the org branch to confirm).
- **Old CFN (deploy/zamboni-cfn.yaml) conventions** — the org branch's prior
  CloudFormation template (naming conventions, VPC/SG patterns, CodePipeline
  wiring specifics) doesn't exist in this repo at all — this repo only has
  CodeBuild/CodeDeploy pieces and an IAM policy JSON with no CFN. Not a gap
  to "re-port" so much as a reference to consult when Phase 6 authors the
  fresh CFN here, and when Phase 7 adapts naming/tags/VPC params org-side.
  
"re-port vacuum gaps 4,5,6,7,8,11,12 from org branch"