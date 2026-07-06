import { useQuery } from '@tanstack/react-query';
import { qs, request, requestPaged } from '../client';
import type { CostsResponse, DryRunView, ExecutionRow } from '../types';

export interface ExecutionsFilter {
  page: number;
  size: number;
  fqn?: string;
  engine?: string;
  status?: string;
  integrity_status?: string;
  from?: string;
  to?: string;
  [key: string]: string | number | boolean | undefined;
}

export function useExecutionsList(params: ExecutionsFilter, refetchInterval?: number) {
  return useQuery({
    queryKey: ['executions', 'list', params],
    queryFn: () => requestPaged<ExecutionRow[]>(`/executions${qs(params)}`),
    refetchInterval,
  });
}

export function useExecutionDetail(id: string | null) {
  return useQuery({
    queryKey: ['executions', 'detail', id],
    queryFn: () => request<ExecutionRow>(`/executions/${id}`),
    enabled: !!id,
  });
}

export function useDryRunView(fqn: string | null) {
  return useQuery({
    queryKey: ['dryrun', fqn],
    queryFn: () => request<DryRunView>(`/dryrun/${fqn}`),
    enabled: !!fqn,
  });
}

export function useCosts(groupBy: string, fromDays: number) {
  return useQuery({
    queryKey: ['costs', groupBy, fromDays],
    queryFn: () => request<CostsResponse>(`/costs${qs({ group_by: groupBy, from: fromDays })}`),
  });
}
