import { useDryRunView } from '../../api/hooks/useExecutions';
import { useTablesSearch } from '../../api/hooks/useTables';

export function useDryRunViewerData(search: string, fqn: string | null) {
  const tables = useTablesSearch(search);
  const dryRun = useDryRunView(fqn);
  return { tables, dryRun };
}
