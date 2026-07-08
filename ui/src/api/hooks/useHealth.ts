import { useQuery } from '@tanstack/react-query';
import { qs, request, requestPaged } from '../client';
import type { ExecutionRow, KpiSet } from '../types';

export function useHealthKpis(env = 'prod') {
  return useQuery({
    queryKey: ['health', 'kpis', env],
    queryFn: () => request<KpiSet>(`/health/kpis${qs({ env })}`),
  });
}

export function useRecentExecutions(page = 1, size = 15) {
  return useQuery({
    queryKey: ['executions', 'list', { page, size }],
    queryFn: () => requestPaged<ExecutionRow[]>(`/executions${qs({ page, size })}`),
  });
}

export function useExecutionsToday(page = 1, size = 15) {
  const today = new Date().toISOString().slice(0, 10);
  return useQuery({
    queryKey: ['executions', 'today', today, page, size],
    queryFn: () =>
      requestPaged<ExecutionRow[]>(`/executions${qs({ page, size, from: today, to: today })}`),
  });
}

export function useFailures7d(page = 1, size = 15) {
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
  return useQuery({
    queryKey: ['executions', 'failures7d', from, to, page, size],
    queryFn: () =>
      requestPaged<ExecutionRow[]>(`/executions${qs({ page, size, from, to, status: 'FAILURE' })}`),
  });
}
