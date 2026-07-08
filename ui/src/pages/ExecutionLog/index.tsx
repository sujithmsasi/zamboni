import { DownloadSimple } from '@phosphor-icons/react';
import { Button, Card, DatePicker, Input, message, Select, Space } from 'antd';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';
import type { ExecutionRow } from '../../api/types';
import { DataGrid } from '../../components/DataGrid';
import { PageHeader } from '../../components/PageHeader';
import { StateBadge } from '../../components/StateBadge';
import { downloadCsv } from '../../utils/csv';
import { ExecutionDetailPanel } from './components/ExecutionDetailPanel';
import { useExecutionLogData } from './hooks';

const { RangePicker } = DatePicker;

const ENGINE_OPTIONS = ['hk', 'archival', 'lifecycle'].map((v) => ({ value: v, label: v }));
const STATUS_OPTIONS = ['SUCCESS', 'FAILURE', 'SKIPPED', 'DRY_RUN', 'RUNNING'].map((v) => ({ value: v, label: v }));

const COLUMNS = [
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

// Not ported from app/pages/7_Execution_Log.py: the SLA Breach Tracker
// (JOIN across stream_registry/hk_config/execution_log with per-frequency
// overdue thresholds) has no equivalent in contracts.md §6 -- closest is
// GET /api/stale?kind=hk, which is a different page (Stale Resources, not
// in this wave) with different semantics (never-run, not overdue-by-cadence).
export default function ExecutionLogPage() {
  const [fqn, setFqn] = useState('');
  const [engine, setEngine] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(15);

  const { list } = useExecutionLogData({
    page, size,
    fqn: fqn || undefined,
    engine, status,
    from: range?.[0]?.format('YYYY-MM-DD'),
    to: range?.[1]?.format('YYYY-MM-DD'),
  });

  const handleExport = () => {
    const rows = list.data?.data ?? [];
    if (rows.length === 0) {
      message.info('No rows to export.');
      return;
    }
    downloadCsv(rows, 'zamboni_execution_log.csv');
  };

  return (
    <div>
      <PageHeader
        title="Execution Log"
        subtitle="Browse and filter engine execution history"
        actions={
          <Button icon={<DownloadSimple size={14} />} onClick={handleExport}>
            Export CSV
          </Button>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="Search table FQN"
            style={{ width: 280 }}
            value={fqn}
            onChange={(e) => { setFqn(e.target.value); setPage(1); }}
            allowClear
          />
          <Select
            placeholder="Engine" allowClear style={{ width: 140 }}
            value={engine} onChange={(v) => { setEngine(v); setPage(1); }} options={ENGINE_OPTIONS}
          />
          <Select
            placeholder="Status" allowClear style={{ width: 140 }}
            value={status} onChange={(v) => { setStatus(v); setPage(1); }} options={STATUS_OPTIONS}
          />
          <RangePicker
            value={range as [Dayjs, Dayjs] | null}
            onChange={(v) => { setRange(v); setPage(1); }}
          />
        </Space>
      </Card>

      <DataGrid<ExecutionRow>
        columns={COLUMNS}
        queryResult={list}
        rowKey="execution_id"
        emptyText="No executions found with the selected filters."
        onPageChange={(p, s) => { setPage(p); setSize(s); }}
        expandable={{ expandedRowRender: (record) => <ExecutionDetailPanel id={record.execution_id} /> }}
      />
    </div>
  );
}
