import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request, requestMultipart } from '../client';
import type { JobRow, MutationResult } from '../types';

export function useJobs(search?: string) {
  return useQuery({
    queryKey: ['jobs', 'list', search],
    queryFn: () => request<JobRow[]>(`/jobs${qs({ search })}`),
  });
}

export function useUpsertJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      request<MutationResult>('/jobs', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

export function useImportJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return requestMultipart<{ imported: number; failed: number; audit_id: string }>('/jobs/import', formData);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

export function useDeleteJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => request<MutationResult>(`/jobs/${name}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });
}
