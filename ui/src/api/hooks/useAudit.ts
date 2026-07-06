import { useQuery } from '@tanstack/react-query';
import { qs, requestPaged } from '../client';
import type { AuditRow } from '../types';

export interface AuditFilter {
  page: number;
  size: number;
  actor?: string;
  action?: string;
  from?: string;
  to?: string;
  [key: string]: string | number | boolean | undefined;
}

export function useAuditList(params: AuditFilter) {
  return useQuery({
    queryKey: ['audit', 'list', params],
    queryFn: () => requestPaged<AuditRow[]>(`/audit${qs(params)}`),
  });
}
