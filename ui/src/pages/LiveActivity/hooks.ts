import { useEffect, useState } from 'react';
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
  const [runningPage, setRunningPage] = useState(1);
  const [runningSize, setRunningSize] = useState(15);
  const running = useExecutionsList(
    { page: runningPage, size: runningSize, status: 'RUNNING' },
    REFRESH_MS,
  );
  const onRunningPageChange = (page: number, size: number) => { setRunningPage(page); setRunningSize(size); };

  const [recentPage, setRecentPage] = useState(1);
  const [recentSize, setRecentSize] = useState(15);
  // A changed filter can leave `recentPage` pointing past the new result set
  // (e.g. paged to 5 under "hk", then switching to "archival") -- reset to 1
  // whenever the filters themselves change, same convention as ExecutionLog.
  useEffect(() => { setRecentPage(1); }, [filters.engine, filters.status]);
  const recent = useExecutionsList(
    { page: recentPage, size: recentSize, engine: filters.engine, status: filters.status },
    REFRESH_MS,
  );
  const onRecentPageChange = (page: number, size: number) => { setRecentPage(page); setRecentSize(size); };

  const locks = useLocks(REFRESH_MS);
  return { running, onRunningPageChange, recent, onRecentPageChange, locks };
}
