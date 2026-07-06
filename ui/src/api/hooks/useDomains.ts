import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request } from '../client';
import type { DomainRow, MutationResult } from '../types';

export function useDomainsList(activeOnly = false) {
  return useQuery({
    queryKey: ['domains', 'list', activeOnly],
    queryFn: () => request<DomainRow[]>(`/domains${qs({ active_only: activeOnly })}`),
  });
}

export function useDomain(name: string | null) {
  return useQuery({
    queryKey: ['domains', 'detail', name],
    queryFn: () => request<DomainRow>(`/domains/${name}`),
    enabled: !!name,
  });
}

export function useCreateDomain() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      request<MutationResult>('/domains', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domains'] }),
  });
}

export function useUpdateDomain() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      request<MutationResult>(`/domains/${name}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domains'] }),
  });
}
