import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from '../client';
import type { EscalationEntry, MutationResult, PlatformSettings } from '../types';

export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: () => request<PlatformSettings>('/settings'),
  });
}

/** PUT merges server-side (settings_svc.update_settings does {**before, **new}) -- callers only need to send the fields their tab owns. */
export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { settings: Record<string, unknown>; dry_run: boolean }) =>
      request<MutationResult>('/settings', { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });
}

export function useEscalationList() {
  return useQuery({
    queryKey: ['escalation', 'list'],
    queryFn: () => request<EscalationEntry[]>('/escalation'),
  });
}

export function useCreateEscalation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { key: string; entry: Record<string, unknown>; dry_run: boolean }) =>
      request<MutationResult>('/escalation', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['escalation'] }),
  });
}

export function useUpdateEscalation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, entry, dry_run }: { key: string; entry: Record<string, unknown>; dry_run: boolean }) =>
      request<MutationResult>(`/escalation/${encodeURIComponent(key)}`, {
        method: 'PUT',
        body: JSON.stringify({ key, entry, dry_run }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['escalation'] }),
  });
}

export function useDeleteEscalation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, dry_run }: { key: string; dry_run: boolean }) =>
      request<MutationResult>(`/escalation/${encodeURIComponent(key)}?dry_run=${dry_run}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['escalation'] }),
  });
}
