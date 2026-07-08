import { useQueries } from '@tanstack/react-query';
import { Card, Col, Collapse, Row, Select, Skeleton, Statistic } from 'antd';
import { useState } from 'react';
import { qs, requestPaged } from '../../../api/client';
import { useLifecycleConfig, useNonprodList } from '../../../api/hooks/useLifecycle';
import type { NonprodRow } from '../../../api/types';
import { DataGrid } from '../../../components/DataGrid';
import { StateBadge } from '../../../components/StateBadge';

const STATES = ['ACTIVE', 'STALE_CANDIDATE', 'GREENZONE', 'PENDING_DROP'] as const;
const STATE_ICONS: Record<string, string> = {
  ACTIVE: '🟢', STALE_CANDIDATE: '🟡', GREENZONE: '🟠', PENDING_DROP: '🔴',
};

function ThresholdsExplainer() {
  const config = useLifecycleConfig();
  if (config.isLoading || !config.data) return <Skeleton active paragraph={{ rows: 2 }} />;
  const { stale_days, greenzone_days, pending_drop_days } = config.data;
  return (
    <div style={{ fontSize: 13, lineHeight: 1.7 }}>
      <p>
        The Lifecycle Engine scans all non-prod Glue databases on every run. A table is tracked from the
        moment it is first discovered. Activity is measured from <code>last_query_at</code> and{' '}
        <code>last_write_at</code> timestamps.
      </p>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 12 }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid #D9E2EC' }}>
            <th style={{ padding: '4px 8px' }}>Stage</th>
            <th style={{ padding: '4px 8px' }}>Default</th>
            <th style={{ padding: '4px 8px' }}>Description</th>
          </tr>
        </thead>
        <tbody>
          {[
            ['ACTIVE → STALE_CANDIDATE', `${stale_days} days inactive`, 'No query or write in this many days'],
            ['STALE_CANDIDATE → GREENZONE', 'Immediate (next scan)', 'Owner is notified via email/SNS. Grace window starts.'],
            ['GREENZONE window', `${greenzone_days} days`, 'Owner can claim or exempt the table during this period'],
            ['GREENZONE → PENDING_DROP', `After ${greenzone_days} days`, 'Final notice sent'],
            ['PENDING_DROP → DROPPED', `${pending_drop_days} days`, 'Table is physically deleted from Glue + S3'],
          ].map(([stage, def, desc]) => (
            <tr key={stage} style={{ borderBottom: '1px solid #F2F4F7' }}>
              <td style={{ padding: '4px 8px', fontWeight: 600 }}>{stage}</td>
              <td style={{ padding: '4px 8px' }}>{def}</td>
              <td style={{ padding: '4px 8px', color: '#667085' }}>{desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ margin: 0 }}>
        Any table can be exempted from the Bulk Exemption / Claim tab — exempt tables stay ACTIVE
        permanently. Backup-pattern tables (<code>_bkp</code>, <code>_backup</code>, <code>_copy</code>) are
        auto-flagged and skip straight to exemption review.
      </p>
    </div>
  );
}

export function StateOverviewTab({ env }: { env: string }) {
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(15);

  const counts = useQueries({
    queries: STATES.map((state) => ({
      queryKey: ['nonprod', 'count', env, state],
      queryFn: () => requestPaged<NonprodRow[]>(`/nonprod${qs({ env, state, page: 1, size: 1 })}`),
    })),
  });

  const list = useNonprodList(env, stateFilter, page, size);

  const columns = [
    { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
    { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 120 },
    {
      title: 'State', dataIndex: 'lifecycle_state', key: 'lifecycle_state', width: 140,
      render: (v: string) => <StateBadge state={v} />,
    },
    { title: 'Days Inactive', dataIndex: 'days_since_activity', key: 'days_since_activity', width: 120 },
    { title: 'GZ Expires', dataIndex: 'greenzone_expires_at', key: 'greenzone_expires_at', width: 150 },
    { title: 'Drop At', dataIndex: 'pending_drop_expires_at', key: 'pending_drop_expires_at', width: 150 },
    {
      title: 'Exempt', dataIndex: 'owner_exempted', key: 'owner_exempted', width: 90,
      render: (v: boolean | null) => (v ? '✅' : ''),
    },
    {
      title: 'Backup', dataIndex: 'is_backup_pattern', key: 'is_backup_pattern', width: 90,
      render: (v: boolean | null) => (v ? '🗂️' : ''),
    },
  ];

  return (
    <div>
      <Collapse
        style={{ marginBottom: 16 }}
        items={[{ key: '1', label: 'ℹ️ How are stale tables identified? What happens?', children: <ThresholdsExplainer /> }]}
      />

      <Row gutter={16} style={{ marginBottom: 16 }}>
        {STATES.map((state, i) => (
          <Col span={6} key={state}>
            <Card size="small">
              <Statistic
                title={`${STATE_ICONS[state]} ${state}`}
                value={counts[i]?.data?.pagination?.total ?? 0}
                loading={counts[i]?.isLoading}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Lifecycle State</div>
        <Select
          style={{ width: 220 }}
          value={stateFilter ?? 'All'}
          onChange={(v) => { setStateFilter(v === 'All' ? undefined : v); setPage(1); }}
          options={['All', ...STATES].map((s) => ({ value: s, label: s }))}
        />
      </div>

      <DataGrid<NonprodRow>
        columns={columns}
        queryResult={list}
        rowKey="table_fqn"
        onPageChange={(p, s) => { setPage(p); setSize(s); }}
        emptyText={`No tables found in ${env} with the selected state.`}
      />
    </div>
  );
}
