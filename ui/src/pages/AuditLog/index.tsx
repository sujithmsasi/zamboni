import { DownloadSimple } from '@phosphor-icons/react';
import { Button, Card, Col, Input, message, Row, Select, Space, Statistic } from 'antd';
import { useState } from 'react';
import type { AuditRow } from '../../api/types';
import { DataGrid } from '../../components/DataGrid';
import { PageHeader } from '../../components/PageHeader';
import { StateBadge } from '../../components/StateBadge';
import { downloadCsv } from '../../utils/csv';
import { AuditDetailPanel } from './components/AuditDetailPanel';
import { useAuditLogData } from './hooks';

const DAYS_OPTIONS = [1, 7, 14, 30, 90].map((d) => ({ value: d, label: `Last ${d} day${d > 1 ? 's' : ''}` }));

// Mirrors engine/core/audit.py::AuditAction — kept as a literal list rather
// than fetched from the backend (no endpoint exposes the enum), same
// approach the Streamlit twin used (introspecting the Python class directly,
// which isn't available to a browser client).
const ACTION_OPTIONS = [
  'domain_create', 'domain_update', 'table_register', 'table_unregister', 'policy_change',
  'bulk_template_apply', 'hk_enable', 'hk_disable', 'dry_run_promote', 'run_hk_now',
  'cancel_query', 'kill_switch', 'circuit_breaker_reenable', 'lifecycle_exemption',
  'claim_table', 'report_export', 'settings_change', 'escalation_change',
  'dry_run_until_set', 'metadata_rollback', 'gate0_override_set',
].sort().map((v) => ({ value: v, label: v }));

const STATUS_OPTIONS = ['SUCCESS', 'FAILURE', 'DRY_RUN', 'REJECTED'].map((v) => ({ value: v, label: v }));

const COLUMNS = [
  { title: 'Timestamp', dataIndex: 'timestamp', key: 'timestamp', width: 170 },
  { title: 'Actor', dataIndex: 'actor', key: 'actor', width: 130 },
  { title: 'Action', dataIndex: 'action_type', key: 'action_type', width: 170 },
  { title: 'Target', dataIndex: 'target_id', key: 'target_id', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 110 },
  { title: 'Dry Run', dataIndex: 'dry_run', key: 'dry_run', width: 90, render: (v: boolean) => (v ? 'Yes' : 'No') },
  {
    title: 'Status', dataIndex: 'status', key: 'status', width: 110,
    render: (s: string) => <StateBadge state={s} />,
  },
  { title: 'Reason', dataIndex: 'reason', key: 'reason', ellipsis: true },
];

// Not ported: Domain / Target ID free-text filters -- GET /api/audit
// (contracts.md §6) only accepts actor/action/from/to/page/size, no
// domain or target_id params. Status is filtered client-side over the
// current page, same approach the Streamlit twin used (it also filtered
// its already-fetched window in pandas rather than in SQL).
export default function AuditLogPage() {
  const [days, setDays] = useState(7);
  const [action, setAction] = useState<string | undefined>(undefined);
  const [actor, setActor] = useState('');
  const [statusFilter, setStatusFilter] = useState<string[]>(['SUCCESS', 'FAILURE', 'REJECTED']);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(50);

  const today = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);

  const { list } = useAuditLogData({ page, size, actor: actor || undefined, action, from, to: today });

  const allRows = list.data?.data ?? [];
  const rows = statusFilter.length === 0 ? allRows : allRows.filter((r) => statusFilter.includes(r.status));

  const failures = rows.filter((r) => r.status === 'FAILURE').length;
  const rejected = rows.filter((r) => r.status === 'REJECTED').length;
  const liveRuns = rows.filter((r) => !r.dry_run).length;

  const handleExport = () => {
    if (rows.length === 0) {
      message.info('No rows to export.');
      return;
    }
    downloadCsv(rows, `zamboni_audit_log_${days}d.csv`);
  };

  return (
    <div>
      <PageHeader
        title="Audit Log"
        subtitle="Complete audit trail of all user and system actions"
        actions={
          <Button icon={<DownloadSimple size={14} />} onClick={handleExport}>
            Export CSV
          </Button>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select style={{ width: 160 }} value={days} onChange={(v) => { setDays(v); setPage(1); }} options={DAYS_OPTIONS} />
          <Select
            allowClear placeholder="Action Type" style={{ width: 220 }}
            value={action} onChange={(v) => { setAction(v); setPage(1); }} options={ACTION_OPTIONS}
          />
          <Select
            mode="multiple" style={{ width: 280 }} placeholder="Status"
            value={statusFilter} onChange={setStatusFilter} options={STATUS_OPTIONS}
          />
          <Input
            placeholder="Actor (username)" style={{ width: 180 }} allowClear
            value={actor} onChange={(e) => { setActor(e.target.value); setPage(1); }}
          />
        </Space>
      </Card>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card size="small"><Statistic title="Total Events" value={list.data?.pagination?.total ?? 0} /></Card></Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="Failures" value={failures} valueStyle={failures > 0 ? { color: '#B42318' } : undefined} />
          </Card>
        </Col>
        <Col span={6}><Card size="small"><Statistic title="Rejected" value={rejected} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="Live Actions" value={liveRuns} /></Card></Col>
      </Row>

      <DataGrid<AuditRow>
        columns={COLUMNS}
        queryResult={{ ...list, data: list.data ? { data: rows, pagination: list.data.pagination } : undefined }}
        rowKey="audit_id"
        emptyText="No audit events found for the selected filters."
        onPageChange={(p, s) => { setPage(p); setSize(s); }}
        expandable={{ expandedRowRender: (record) => <AuditDetailPanel row={record} /> }}
      />
    </div>
  );
}
