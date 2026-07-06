import { useQuery } from '@tanstack/react-query';
import { qs, request, requestPaged } from '../client';
import type { ExecutionRow, KpiSet } from '../types';

export function useHealthKpis(env = 'prod') {
  return useQuery({
    queryKey: ['health', 'kpis', env],
    queryFn: () => request<KpiSet>(`/health/kpis${qs({ env })}`),
  });
}

export function useRecentExecutions(size = 10) {
  return useQuery({
    queryKey: ['executions', 'list', { page: 1, size }],
    queryFn: () => requestPaged<ExecutionRow[]>(`/executions${qs({ page: 1, size })}`),
  });
}

export function useExecutionsToday(size = 50) {
  const today = new Date().toISOString().slice(0, 10);
  return useQuery({
    queryKey: ['executions', 'today', today, size],
    queryFn: () =>
      requestPaged<ExecutionRow[]>(`/executions${qs({ page: 1, size, from: today, to: today })}`),
  });
}

export function useFailures7d(size = 50) {
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
  return useQuery({
    queryKey: ['executions', 'failures7d', from, to, size],
    queryFn: () =>
      requestPaged<ExecutionRow[]>(`/executions${qs({ page: 1, size, from, to, status: 'FAILURE' })}`),
  });
}
