import type { ExecutionsFilter } from '../../api/hooks/useExecutions';
import { useExecutionDetail, useExecutionsList } from '../../api/hooks/useExecutions';

export function useExecutionLogData(filters: ExecutionsFilter) {
  return { list: useExecutionsList(filters) };
}

export { useExecutionDetail };
