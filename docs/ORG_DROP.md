# ORG_DROP.md — Clean Drop Delivery Protocol (contracts §10)
# Decided 2026-07-05: no merging with the old org branch. Time-optimal path.

## Delivery
1. Finish Phases 0–6 on the personal repo; final commit pushed to public dev.
2. Transfer the codebase to the org laptop (approved channel; it is
   program-generated code — no org IP round-trip involved).
3. Run 07_org_drop.md with Bedrock Sonnet (5 if available, else 4.6):
   new branch `new-phase1` → drop → adapt CFN params → deploy → smoke test.

## What is intentionally left behind (re-port backlog, post-showcase, org-side)
See .claude/org_divergence.md — vacuum 12-gap-type hardening, Parquet log
writer, CloudTrail signals, old CFN conventions. Bedrock Claude on the org
laptop can read BOTH branches to re-port these into new-phase1 later.

## Rollback story
The old org branch remains untouched and deployable — it IS the rollback.
