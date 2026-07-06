import { useExecutionsList } from '../../api/hooks/useExecutions';
import { useLocks } from '../../api/hooks/useSystem';

// LiveActivity is the one page in this wave with a live refetchInterval
// (contracts.md-adjacent phase brief: "auto-refresh: useQuery refetchInterval
// 10_000 on this page only") -- replaces the Streamlit twin's blocking
// `time.sleep(30)` + manual toggle with real background polling.
const REFRESH_MS = 10_000;

interface LiveActivityFilters {
  engine?: string;
  status?: string;
}

export function useLiveActivityData(filters: LiveActivityFilters) {
  const running = useExecutionsList({ page: 1, size: 50, status: 'RUNNING' }, REFRESH_MS);
  const recent = useExecutionsList(
    { page: 1, size: 100, engine: filters.engine, status: filters.status },
    REFRESH_MS,
  );
  const locks = useLocks(REFRESH_MS);
  return { running, recent, locks };
}
