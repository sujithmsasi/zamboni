import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request, requestPaged } from '../client';
import type { ConflictRow, GatesInfo, MutationResult, RescanResult } from '../types';

export function useGatesDetail(fqn: string | null) {
  return useQuery({
    queryKey: ['gates', 'detail', fqn],
    queryFn: () => request<GatesInfo>(`/gates/${fqn}`),
    enabled: !!fqn,
  });
}

export function useUpdateGates() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ fqn, body }: { fqn: string; body: Record<string, unknown> }) =>
      request<MutationResult>(`/gates/${fqn}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: (_r, { fqn }) => {
      queryClient.invalidateQueries({ queryKey: ['gates', 'detail', fqn] });
      queryClient.invalidateQueries({ queryKey: ['conflicts'] });
    },
  });
}

export function useConflicts(page: number, size: number, domain?: string) {
  return useQuery({
    queryKey: ['conflicts', 'list', { page, size, domain }],
    queryFn: () => requestPaged<ConflictRow[]>(`/conflicts${qs({ page, size, domain })}`),
  });
}

/** export=csv returns the full unpaginated JSON row set, not a real CSV stream — see utils/csv.ts. */
export function useConflictsExportAll(domain?: string) {
  return useQuery({
    queryKey: ['conflicts', 'exportAll', domain],
    queryFn: () => requestPaged<ConflictRow[]>(`/conflicts${qs({ page: 1, size: 1000, domain, export: 'csv' })}`),
    enabled: false,
  });
}

export function useRescanConflicts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (fqns?: string[]) =>
      request<RescanResult>('/conflicts/rescan', {
        method: 'POST',
        body: JSON.stringify({ fqns: fqns ?? null }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conflicts'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
    },
  });
}
