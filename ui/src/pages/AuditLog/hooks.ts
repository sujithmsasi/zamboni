import type { AuditFilter } from '../../api/hooks/useAudit';
import { useAuditList } from '../../api/hooks/useAudit';

export function useAuditLogData(filters: AuditFilter) {
  return { list: useAuditList(filters) };
}
