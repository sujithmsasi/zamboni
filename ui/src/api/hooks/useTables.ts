import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request, requestMultipart, requestPaged } from '../client';
import type { GlueTableRow, JobMappingRow, MutationResult, RegisterBulkResult, TableRow } from '../types';

/**
 * Minimal search-only hook for DryRunViewer's table picker (contracts.md §6
 * GET /api/tables?search=). Table Registration itself is a later wave's page
 * -- this doesn't attempt full CRUD, just enough to feed a search Select.
 *
 * By default gated on a non-empty search (the "type to search" pickers --
 * DryRunViewer, Edit Table, Engine Flags -- show a hint instead of a huge
 * unfiltered dropdown until the user types). Pass `{ enabled: true }` to
 * always fetch, e.g. a suggestion dropdown that should list values as soon
 * as it's opened (Registered Tables / View Configs search boxes).
 */
export function useTablesSearch(search: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['tables', 'search', search],
    queryFn: () => requestPaged<TableRow[]>(`/tables${qs({ page: 1, size: 20, search })}`),
    enabled: options?.enabled ?? search.length > 0,
  });
}

export interface TablesFilter {
  page: number;
  size: number;
  domain?: string;
  layer?: string;
  tier?: string;
  env?: string;
  search?: string;
  database_name?: string;
  [key: string]: string | number | boolean | undefined;
}

export function useTablesList(params: TablesFilter) {
  return useQuery({
    queryKey: ['tables', 'list', params],
    queryFn: () => requestPaged<TableRow[]>(`/tables${qs(params)}`),
  });
}

export function useTableDetail(fqn: string | null) {
  return useQuery({
    queryKey: ['tables', 'detail', fqn],
    queryFn: () => request<TableRow>(`/tables/${fqn}`),
    enabled: !!fqn,
  });
}

// Bulk counterpart of POST /api/tables/register -- one request registers
// every selected table, with the audit trail batched into a single
// Athena INSERT server-side instead of one per table (was ~2-3s per table
// via N sequential single-row register calls; see
// api/routers/tables.py::register_tables_bulk's docstring).
export function useRegisterTablesBulk() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      request<RegisterBulkResult>('/tables/register-bulk', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tables'] }),
  });
}

export function useUpdateTable() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ fqn, body }: { fqn: string; body: Record<string, unknown> }) =>
      request<MutationResult>(`/tables/${fqn}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tables'] }),
  });
}

export interface BulkControlMBody {
  filters: Record<string, unknown>;
  set_fields: Record<string, unknown>;
  dry_run: boolean;
}

export function useBulkControlM() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkControlMBody) =>
      request<MutationResult & { affected: number }>('/tables/bulk-controlm', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tables'] });
      // A real (non-dry-run) apply auto-registers controlm_pipeline_job /
      // controlm_hk_job into the Control-M Job Registry if not already
      // there -- refresh Job List so it shows up without a manual refresh.
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

// Matches GLUE_CATALOG_CACHE_TTL_HOURS (config/settings.py, default 24h) --
// the server already caches this for a day, so there's no reason for the
// browser to ask again sooner on its own. useRescanGlueDatabases/
// useRescanGlueTables are the explicit bypass (the "Rescan" button).
// Exported so BrowseRegisterTab's "All Databases" useQueries fan-out (which
// can't go through the hooks above, since its query count is dynamic) can
// use the same value.
export const GLUE_CACHE_STALE_TIME_MS = 24 * 60 * 60 * 1000;

export function useGlueDatabases() {
  return useQuery({
    queryKey: ['glue', 'databases'],
    queryFn: () => request<string[]>('/glue/databases'),
    staleTime: GLUE_CACHE_STALE_TIME_MS,
  });
}

export function useGlueTables(db: string | null, pattern: string, unregisteredOnly: boolean) {
  return useQuery({
    queryKey: ['glue', 'tables', db, pattern, unregisteredOnly],
    queryFn: () => request<GlueTableRow[]>(`/glue/tables/${db}${qs({ pattern, unregistered_only: unregisteredOnly })}`),
    enabled: !!db,
    staleTime: GLUE_CACHE_STALE_TIME_MS,
  });
}

export function useRescanGlueDatabases() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => request<string[]>('/glue/rescan/databases', { method: 'POST' }),
    onSuccess: (data) => {
      queryClient.setQueryData(['glue', 'databases'], data);
      queryClient.invalidateQueries({ queryKey: ['glue', 'tables'] });
    },
  });
}

export function useRescanGlueTables() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ db, pattern, unregisteredOnly }: { db: string; pattern: string; unregisteredOnly: boolean }) =>
      request<GlueTableRow[]>(
        `/glue/rescan/tables/${db}${qs({ pattern, unregistered_only: unregisteredOnly })}`,
        { method: 'POST' },
      ),
    onSuccess: (data, { db, pattern, unregisteredOnly }) => {
      queryClient.setQueryData(['glue', 'tables', db, pattern, unregisteredOnly], data);
    },
  });
}

export function useImportJobMapping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, dryRun }: { file: File; dryRun: boolean }) => {
      const formData = new FormData();
      formData.append('file', file);
      return requestMultipart<{ rows: JobMappingRow[]; audit_id: string }>(
        `/tables/job-mapping/import${qs({ dry_run: dryRun })}`,
        formData,
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tables'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

export function useExportJobMapping() {
  return useQuery({
    queryKey: ['tables', 'job-mapping', 'export'],
    queryFn: () => request<{ csv: string }>('/tables/job-mapping/export'),
    enabled: false,
  });
}
