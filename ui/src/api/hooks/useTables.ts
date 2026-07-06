import { useQuery } from '@tanstack/react-query';
import { qs, requestPaged } from '../client';
import type { TableRow } from '../types';

/**
 * Minimal search-only hook for DryRunViewer's table picker (contracts.md §6
 * GET /api/tables?search=). Table Registration itself is a later wave's page
 * -- this doesn't attempt full CRUD, just enough to feed a search Select.
 */
export function useTablesSearch(search: string) {
  return useQuery({
    queryKey: ['tables', 'search', search],
    queryFn: () => requestPaged<TableRow[]>(`/tables${qs({ page: 1, size: 20, search })}`),
    enabled: search.length > 0,
  });
}
