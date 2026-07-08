// Env selection is genuinely page-level (mirrors 9_NonProd_Lifecycle.py's
// selectbox sitting above all 4 tabs) -- everything else each tab needs is
// scoped by its own filters, so tabs call api/hooks/useLifecycle directly
// (same precedent as PolicyConfig's per-tab hook calls, see ui/PATTERN.md).
export const NONPROD_ENVIRONMENTS = ['preprod', 'dev', 'test'];
