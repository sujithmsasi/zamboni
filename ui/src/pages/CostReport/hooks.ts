import { useCosts } from '../../api/hooks/useExecutions';

export function useCostReportData(groupBy: string, fromDays: number) {
  return { costs: useCosts(groupBy, fromDays) };
}
