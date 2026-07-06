import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from '../client';
import type { LockRow, SystemMode } from '../types';

export function useSystemMode() {
  return useQuery({
    queryKey: ['system', 'mode'],
    queryFn: () => request<SystemMode>('/system/mode'),
  });
}

export function useLocks(refetchInterval?: number) {
  return useQuery({
    queryKey: ['system', 'locks'],
    queryFn: () => request<LockRow[]>('/locks'),
    refetchInterval,
  });
}

export function useReleaseLock() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (fqn: string) => request<{ released: boolean }>(`/locks/${fqn}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['system', 'locks'] }),
  });
}
