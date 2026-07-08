import { Alert, Select, Table } from 'antd';
import { useState } from 'react';
import { useStaleNonprod } from '../../../api/hooks/useStale';
import type { NonprodRow } from '../../../api/types';
import { StateBadge } from '../../../components/StateBadge';
import { useStaleResourcesData } from '../hooks';

const columns = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 120 },
  { title: 'Environment', dataIndex: 'environment', key: 'environment', width: 110 },
  {
    title: 'State', dataIndex: 'lifecycle_state', key: 'lifecycle_state', width: 140,
    render: (v: string) => <StateBadge state={v} />,
  },
  { title: 'Days Inactive', dataIndex: 'days_since_activity', key: 'days_since_activity', width: 120 },
  { title: 'Last Query', dataIndex: 'last_query_at', key: 'last_query_at', width: 170 },
  { title: 'Last Write', dataIndex: 'last_write_at', key: 'last_write_at', width: 170 },
  {
    title: 'Backup', dataIndex: 'is_backup_pattern', key: 'is_backup_pattern', width: 90,
    render: (v: boolean | null) => (v ? '🗂️' : ''),
  },
];

/** NonProd Stale tab -- promoted out of 10_Stale_Resources.py tab1's inline
 * sub-section into its own tab, per the phase brief's explicit tab list. */
export function NonprodStaleTab() {
  const [domain, setDomain] = useState<string | undefined>();
  const { domains } = useStaleResourcesData();
  const query = useStaleNonprod(domain);
  const rows = query.data ?? [];

  return (
    <div>
      <div style={{ marginBottom: 16, maxWidth: 280 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain</div>
        <Select
          style={{ width: '100%' }} allowClear placeholder="All"
          value={domain} onChange={setDomain}
          options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
        />
      </div>
      {!query.isLoading && rows.length === 0 && <Alert type="success" showIcon message="No stale non-prod tables." />}
      {rows.length > 0 && (
        <Table<NonprodRow> size="small" loading={query.isLoading} columns={columns} dataSource={rows} rowKey="table_fqn" pagination={{ pageSize: 15 }} />
      )}
    </div>
  );
}
