import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request, requestPaged } from '../client';
import type { MutationResult, PolicyDetail, PolicyViewRow, TemplatesMap } from '../types';

export interface PoliciesFilter {
  page: number;
  size: number;
  domain?: string;
  layer?: string;
  tier?: string;
  [key: string]: string | number | boolean | undefined;
}

export function usePoliciesList(params: PoliciesFilter) {
  return useQuery({
    queryKey: ['policies', 'list', params],
    queryFn: () => requestPaged<PolicyViewRow[]>(`/policies${qs(params)}`),
  });
}

export function usePolicyDetail(fqn: string | null) {
  return useQuery({
    queryKey: ['policies', 'detail', fqn],
    queryFn: () => request<PolicyDetail>(`/policies/${fqn}`),
    enabled: !!fqn,
  });
}

export function useUpdatePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ fqn, body }: { fqn: string; body: Record<string, unknown> }) =>
      request<MutationResult>(`/policies/${fqn}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: (_r, { fqn }) => {
      queryClient.invalidateQueries({ queryKey: ['policies'] });
      queryClient.invalidateQueries({ queryKey: ['policies', 'detail', fqn] });
    },
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: ['templates', 'list'],
    queryFn: () => request<TemplatesMap>('/templates'),
  });
}

export function useUpdateTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      request<MutationResult>(`/templates/${name}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['templates'] }),
  });
}

export function useCreateTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      request<MutationResult>('/templates', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['templates'] }),
  });
}

export function useDeleteTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => request<MutationResult>(`/templates/${name}?dry_run=false`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['templates'] }),
  });
}

export function useApplyTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      request<MutationResult & { affected: number }>(`/templates/${name}/apply`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['policies'] }),
  });
}
