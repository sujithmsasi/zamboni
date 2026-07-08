import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request, requestPaged } from '../client';
import type { DeletionRow, LifecycleConfig, MutationResult, NonprodRow } from '../types';

export function useLifecycleConfig() {
  return useQuery({
    queryKey: ['lifecycle', 'config'],
    queryFn: () => request<LifecycleConfig>('/lifecycle/config'),
    staleTime: 10 * 60 * 1000,
  });
}

export function useNonprodList(env: string, state: string | undefined, page: number, size: number) {
  return useQuery({
    queryKey: ['nonprod', 'list', { env, state, page, size }],
    queryFn: () => requestPaged<NonprodRow[]>(`/nonprod${qs({ env, state, page, size })}`),
  });
}

export function useExemptTables() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { fqns: string[]; reason: string; dry_run: boolean }) =>
      request<MutationResult & { affected: number }>('/nonprod/exempt', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['nonprod'] }),
  });
}

export function useClaimTables() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { fqns: string[]; reason: string; dry_run: boolean }) =>
      request<MutationResult & { affected: number }>('/nonprod/claim', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['nonprod'] }),
  });
}

export function useDeletionHistory(env: string, page: number, size: number) {
  return useQuery({
    queryKey: ['nonprod', 'deletions', { env, page, size }],
    queryFn: () => requestPaged<DeletionRow[]>(`/nonprod/deletions${qs({ env, page, size })}`),
  });
}
