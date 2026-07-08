import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qs, request, requestMultipart } from '../client';
import type { JobRow, MutationResult, TableRow } from '../types';

export function useJobs(search?: string) {
  return useQuery({
    queryKey: ['jobs', 'list', search],
    queryFn: () => request<JobRow[]>(`/jobs${qs({ search })}`),
  });
}

export function useJobMappedTables(jobName: string | null) {
  return useQuery({
    queryKey: ['jobs', 'mapped-tables', jobName],
    queryFn: () => request<TableRow[]>(`/jobs/${encodeURIComponent(jobName ?? '')}/tables`),
    enabled: !!jobName,
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

export interface JobImportRow {
  job_name: string;
  job_type: string;
  domain: string;
  description: string;
  expected_start_time: string;
  expected_duration_min: number;
  job_frequency: string;
}

export interface JobImportResult {
  imported: number;
  failed: number;
  rows: JobImportRow[];
  audit_id?: string;
}

export function useImportJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, dryRun, excludeJobNames }: { file: File; dryRun: boolean; excludeJobNames?: string[] }) => {
      const formData = new FormData();
      formData.append('file', file);
      const query = qs({ dry_run: dryRun, exclude: excludeJobNames?.length ? excludeJobNames.join(',') : undefined });
      return requestMultipart<JobImportResult>(`/jobs/import${query}`, formData);
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
