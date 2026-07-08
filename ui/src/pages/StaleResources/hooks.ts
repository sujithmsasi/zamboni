import { useDomainsList } from '../../api/hooks/useDomains';

/** Domain list is used as a filter across the Stale HK, Zero-Row, and NonProd Stale tabs. */
export function useStaleResourcesData() {
  const domains = useDomainsList(true);
  return { domains };
}

export const STALE_ENVIRONMENTS = ['prod', 'preprod', 'dev', 'test'];
