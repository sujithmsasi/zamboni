import { useDomainsList } from '../../api/hooks/useDomains';

/** Domain list is used across the Browse/Register, Edit, and Engine Flags tabs. */
export function useTableRegistrationData() {
  const domains = useDomainsList(true);
  return { domains };
}
