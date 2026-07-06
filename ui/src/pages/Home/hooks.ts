import { useExecutionsToday, useFailures7d, useHealthKpis, useRecentExecutions } from '../../api/hooks/useHealth';
import { useLocks } from '../../api/hooks/useSystem';

// Every useQuery Home needs lives here — index.tsx stays layout-only. This
// split is the canonical pattern Waves 1-2 replicate (ui/PATTERN.md).
export function useHomeData() {
  const kpis = useHealthKpis();
  const executionsToday = useExecutionsToday();
  const recentExecutions = useRecentExecutions(10);
  const locks = useLocks();
  const failures7d = useFailures7d();

  return { kpis, executionsToday, recentExecutions, locks, failures7d };
}
