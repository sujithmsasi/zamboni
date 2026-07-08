import { Button, Card, Select, Space } from 'antd';
import { useState } from 'react';
import type { ExecutionRow } from '../../api/types';
import { DataGrid } from '../../components/DataGrid';
import { PageHeader } from '../../components/PageHeader';
import { StateBadge } from '../../components/StateBadge';
import { LocksStrip } from './components/LocksStrip';
import { useLiveActivityData } from './hooks';

const RUNNING_COLUMNS = [
  { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 170 },
  { title: 'Engine', dataIndex: 'engine', key: 'engine', width: 90 },
  { title: 'Operation', dataIndex: 'operation', key: 'operation', width: 120 },
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 110 },
  { title: 'Tier', dataIndex: 'tier', key: 'tier', width: 90 },
];

const RECENT_COLUMNS = [
  { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 170 },
  { title: 'Engine', dataIndex: 'engine', key: 'engine', width: 90 },
  { title: 'Operation', dataIndex: 'operation', key: 'operation', width: 120 },
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 110 },
  { title: 'Layer', dataIndex: 'layer', key: 'layer', width: 90 },
  { title: 'Tier', dataIndex: 'tier', key: 'tier', width: 90 },
  {
    title: 'Status', dataIndex: 'status', key: 'status', width: 110,
    render: (status: string) => <StateBadge state={status} />,
  },
  { title: 'Duration (s)', dataIndex: 'duration_seconds', key: 'duration_seconds', width: 110 },
  { title: 'Skip Reason', dataIndex: 'skip_reason', key: 'skip_reason', ellipsis: true },
  { title: 'Error', dataIndex: 'error_message', key: 'error_message', ellipsis: true },
];

const ENGINE_OPTIONS = ['hk', 'archival', 'lifecycle'].map((v) => ({ value: v, label: v }));
const STATUS_OPTIONS = ['SUCCESS', 'FAILURE', 'SKIPPED', 'DRY_RUN', 'RUNNING'].map((v) => ({ value: v, label: v }));

// Note: the Streamlit twin's "Currently Running" grid grouped rows by
// run_id/engine/domain with COUNT(*) — no such aggregation endpoint exists
// in contracts.md §6 (only row-level GET /api/executions), and the phase
// brief's endpoint list for this page names exactly that route. This shows
// the same RUNNING-filtered rows at the row level instead. Its "Today's
// Engine Summary" aggregate table has no backend equivalent either and
// isn't ported for the same reason.
export default function LiveActivityPage() {
  const [engine, setEngine] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const {
    running, onRunningPageChange,
    recent, onRecentPageChange,
    locks,
  } = useLiveActivityData({ engine, status });

  return (
    <div>
      <PageHeader
        title="Live Activity"
        subtitle="Real-time engine activity — refreshes every 10s"
        actions={
          <Button
            onClick={() => {
              running.refetch();
              recent.refetch();
              locks.refetch();
            }}
          >
            🔄 Refresh now
          </Button>
        }
      />

      <LocksStrip queryResult={locks} />

      <Card size="small" title="⚡ Currently Running" style={{ marginBottom: 16 }}>
        <DataGrid<ExecutionRow>
          columns={RUNNING_COLUMNS}
          queryResult={running}
          rowKey="execution_id"
          emptyText="No engines currently running."
          onPageChange={onRunningPageChange}
        />
      </Card>

      <Card size="small" title="📜 Recent Operations">
        <DataGrid<ExecutionRow>
          columns={RECENT_COLUMNS}
          queryResult={recent}
          rowKey="execution_id"
          emptyText="No operations found with the selected filters."
          onPageChange={onRecentPageChange}
          toolbar={
            <Space>
              <Select placeholder="Engine" allowClear style={{ width: 150 }} value={engine} onChange={setEngine} options={ENGINE_OPTIONS} />
              <Select placeholder="Status" allowClear style={{ width: 150 }} value={status} onChange={setStatus} options={STATUS_OPTIONS} />
            </Space>
          }
        />
      </Card>
    </div>
  );
}
