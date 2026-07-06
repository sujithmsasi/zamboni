import { useHealthKpis } from '../../api/hooks/useHealth';

// Every useQuery this page needs lives here — index.tsx stays layout-only
// (ui/PATTERN.md). health_kpis already carries everything the Health
// Dashboard needs (contracts.md §6: "home + health dashboard numbers").
export function useHealthDashboardData() {
  const kpis = useHealthKpis();
  return { kpis };
}
