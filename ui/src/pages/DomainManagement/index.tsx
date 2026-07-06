import { Button, Table, Tag } from 'antd';
import { useState } from 'react';
import type { DomainRow } from '../../api/types';
import { PageHeader } from '../../components/PageHeader';
import { DomainFormModal } from './components/DomainFormModal';
import { useDomainManagementData } from './hooks';

const yesNo = (v: boolean) => (v ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>);

// Domain + Display Name collapsed into one column: domain_name is the real
// key used everywhere else in the app (URLs, filters, other tables' FK) and
// is always present; display_name is optional and unset for most seeded
// domains (confirmed via /api/domains -- would otherwise show blank cells).
// Shown as a bold primary line with the display name as a lighter subtitle
// only when it's actually set and differs from the key.
const COLUMNS = (onEdit: (row: DomainRow) => void) => [
  {
    title: 'Domain', dataIndex: 'domain_name', key: 'domain_name',
    render: (_: string, record: DomainRow) => (
      <div>
        <div style={{ fontWeight: 600 }}>{record.domain_name}</div>
        {record.display_name && record.display_name !== record.domain_name && (
          <div style={{ fontSize: 12, color: '#667085' }}>{record.display_name}</div>
        )}
      </div>
    ),
  },
  { title: 'Tables', dataIndex: 'table_count', key: 'table_count', width: 90 },
  { title: 'Owner Email', dataIndex: 'owner_email', key: 'owner_email' },
  { title: 'Archival', dataIndex: 'archive_enabled', key: 'archive_enabled', width: 90, render: yesNo },
  { title: 'Hot Retention (d)', dataIndex: 'hot_retention_days', key: 'hot_retention_days', width: 140 },
  { title: 'Archive Duration (d)', dataIndex: 'archive_duration_days', key: 'archive_duration_days', width: 160 },
  { title: 'Stale Threshold (d)', dataIndex: 'stale_threshold_days', key: 'stale_threshold_days', width: 150 },
  { title: 'Auto-Delete After (d)', dataIndex: 'auto_delete_after_days', key: 'auto_delete_after_days', width: 160 },
  { title: 'Active', dataIndex: 'is_active', key: 'is_active', width: 90, render: yesNo },
  { title: 'Digest', dataIndex: 'digest_enabled', key: 'digest_enabled', width: 90, render: yesNo },
  { title: 'Registered', dataIndex: 'registered_at', key: 'registered_at', width: 120, render: (v: string) => v?.slice(0, 10) },
  {
    title: '', key: 'actions', width: 80,
    render: (_: unknown, record: DomainRow) => (
      <Button size="small" onClick={() => onEdit(record)}>Edit</Button>
    ),
  },
];

// GET /api/domains returns a bare array (no pagination envelope), so this
// intentionally uses a plain AntD Table rather than <DataGrid> -- same
// exception documented on UnhealthyTablesGrid/LocksStrip.
export default function DomainManagementPage() {
  const { domains } = useDomainManagementData();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<DomainRow | null>(null);

  return (
    <div>
      <PageHeader
        title="Domain Management"
        subtitle="Register and manage the top-level domains every table belongs to"
        actions={
          <Button type="primary" onClick={() => { setEditing(null); setModalOpen(true); }}>
            + Register New
          </Button>
        }
      />

      <Table<DomainRow>
        size="small"
        loading={domains.isLoading}
        columns={COLUMNS((row) => { setEditing(row); setModalOpen(true); })}
        dataSource={domains.data ?? []}
        rowKey="domain_name"
        pagination={(domains.data?.length ?? 0) > 15 ? { pageSize: 15 } : false}
      />

      <DomainFormModal open={modalOpen} domain={editing} onClose={() => setModalOpen(false)} />
    </div>
  );
}
