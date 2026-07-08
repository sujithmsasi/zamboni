import { useQuery } from '@tanstack/react-query';
import { qs, request } from '../client';
import type { NonprodRow, StaleHkRow, StaleOrphanRow, StaleZeroRowRow } from '../types';

export function useStaleHk(domain: string | undefined, environment: string, days: number) {
  return useQuery({
    queryKey: ['stale', 'hk', { domain, environment, days }],
    queryFn: () => request<StaleHkRow[]>(`/stale${qs({ kind: 'hk', domain, environment, days })}`),
  });
}

export function useStaleZeroRow(domain: string | undefined, threshold: number, enabled: boolean) {
  return useQuery({
    queryKey: ['stale', 'zero_row', { domain, threshold }],
    queryFn: () => request<StaleZeroRowRow[]>(`/stale${qs({ kind: 'zero_row', domain, threshold })}`),
    enabled,
  });
}

export function useStaleNonprod(domain: string | undefined) {
  return useQuery({
    queryKey: ['stale', 'nonprod', { domain }],
    queryFn: () => request<NonprodRow[]>(`/stale${qs({ kind: 'nonprod', domain })}`),
  });
}

export function useStaleOrphan(prefix: string, enabled: boolean) {
  return useQuery({
    queryKey: ['stale', 'orphan', { prefix }],
    queryFn: () => request<StaleOrphanRow[]>(`/stale${qs({ kind: 'orphan', prefix })}`),
    enabled,
  });
}
