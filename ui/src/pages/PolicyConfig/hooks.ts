import { useDomainsList } from '../../api/hooks/useDomains';
import { useSystemMode } from '../../api/hooks/useSystem';

/** Domain list + Gate 0 override cap are used across the Edit/Bulk/Templates tabs. */
export function usePolicyConfigData() {
  const domains = useDomainsList(true);
  const systemMode = useSystemMode();
  return { domains, systemMode };
}
