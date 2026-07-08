import { useState } from 'react';
import { useExecutionsToday, useFailures7d, useHealthKpis, useRecentExecutions } from '../../api/hooks/useHealth';
import { useLocks } from '../../api/hooks/useSystem';

// Every useQuery Home needs lives here — index.tsx stays layout-only. This
// split is the canonical pattern Waves 1-2 replicate (ui/PATTERN.md).
//
// Each of the three execution grids (Recent Activity + the two KPI-card
// drill-down modals) owns its own page/size state here rather than a shared
// pair, since they're independently paginated surfaces -- paging the
// Executions Today modal must not move Recent Activity's page underneath it.
export function useHomeData() {
  const kpis = useHealthKpis();
  const locks = useLocks();

  const [recentPage, setRecentPage] = useState(1);
  const [recentSize, setRecentSize] = useState(10);
  const recentExecutions = useRecentExecutions(recentPage, recentSize);
  const onRecentPageChange = (page: number, size: number) => { setRecentPage(page); setRecentSize(size); };

  const [todayPage, setTodayPage] = useState(1);
  const [todaySize, setTodaySize] = useState(50);
  const executionsToday = useExecutionsToday(todayPage, todaySize);
  const onExecutionsTodayPageChange = (page: number, size: number) => { setTodayPage(page); setTodaySize(size); };

  const [failuresPage, setFailuresPage] = useState(1);
  const [failuresSize, setFailuresSize] = useState(50);
  const failures7d = useFailures7d(failuresPage, failuresSize);
  const onFailures7dPageChange = (page: number, size: number) => { setFailuresPage(page); setFailuresSize(size); };

  return {
    kpis,
    locks,
    recentExecutions,
    onRecentPageChange,
    executionsToday,
    onExecutionsTodayPageChange,
    failures7d,
    onFailures7dPageChange,
  };
}
