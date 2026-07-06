# The Home Pattern — canonical, replicate exactly (Waves 1-2)

## Folder shape

```
pages/<PageName>/
├── index.tsx      # layout ONLY — no useQuery calls here
├── hooks.ts        # every useQuery the page needs, composed from api/hooks/*
└── components/     # local-only components, if the page needs any
```

`index.tsx` calls one aggregate hook (e.g. `useHomeData()`) from `hooks.ts` and
renders. `hooks.ts` imports the reusable per-router hooks from
`api/hooks/use<Router>.ts` and composes them — it does not call `fetch`/
`request` directly. This split keeps data-fetching testable/reusable and
keeps the page component readable as pure layout.

## Query keys convention

`['domain', 'resource', ...params]` — e.g. `['health', 'kpis', env]`,
`['executions', 'list', { page, size }]`. Keep params in the key so
TanStack Query caches per-filter-combination and mutations can
`invalidateQueries({ queryKey: ['domain'] })` to bust every variant.

## DataGrid usage

Every table goes through `<DataGrid>` (`components/DataGrid.tsx`) — never a
raw AntD `<Table>`. Pass `columns`, the raw `useQuery` result as
`queryResult`, `rowKey`, and optionally `rowSelection`/`onPageChange`/
`toolbar`/`emptyText`. `DataGrid` owns loading (AntD `loading` prop),
error (`Alert` + retry calling `refetch()`), and empty (`Empty` with a
custom hint) states internally — callers don't reimplement any of that.

## Mutation recipe

```ts
const mutation = useMutation({
  mutationFn: (body) => request<MutationResult>('/some/path', { method: 'POST', body: JSON.stringify(body) }),
  onSuccess: (result) => {
    queryClient.invalidateQueries({ queryKey: ['domain'] });
    message.success(`Saved (audit: ${result.audit_id})`);
  },
});
```

Every mutation: `dry_run` comes from the global system-mode context, a
confirm `Modal` gates destructive actions, and the success message always
surfaces `audit_id`.

## Loading / error / empty (non-grid content, e.g. KPI cards)

- Loading → `<Skeleton active />` (or `Skeleton.Input` for inline stats).
- Error → `<Alert type="error" showIcon action={<Button onClick={refetch}>Retry</Button>} />`.
- Empty → `<Empty description="..." />` with a hint specific to the
  resource, not a generic "no data."

## No new patterns

Deviating from any of the above in a Wave 1-2 page requires a note in
`.claude/decisions.md` and Sujith's sign-off — see contracts.md §7.
