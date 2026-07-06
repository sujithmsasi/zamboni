import { useQuery } from '@tanstack/react-query';
import { request } from '../client';
import type { LockRow, SystemMode } from '../types';

export function useSystemMode() {
  return useQuery({
    queryKey: ['system', 'mode'],
    queryFn: () => request<SystemMode>('/system/mode'),
  });
}

export function useLocks() {
  return useQuery({
    queryKey: ['system', 'locks'],
    queryFn: () => request<LockRow[]>('/locks'),
  });
}
