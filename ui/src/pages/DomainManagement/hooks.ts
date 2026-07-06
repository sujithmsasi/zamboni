import { useCreateDomain, useDomainsList, useUpdateDomain } from '../../api/hooks/useDomains';

export function useDomainManagementData() {
  const domains = useDomainsList(false);
  const create = useCreateDomain();
  const update = useUpdateDomain();
  return { domains, create, update };
}
