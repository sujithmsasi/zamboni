import { DownloadSimple } from '@phosphor-icons/react';
import { Button, Card, Col, message, Row, Statistic } from 'antd';
import { useState } from 'react';
import { useDeletionHistory } from '../../../api/hooks/useLifecycle';
import type { DeletionRow } from '../../../api/types';
import { DataGrid } from '../../../components/DataGrid';
import { downloadCsv } from '../../../utils/csv';

const columns = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 120 },
  { title: 'Environment', dataIndex: 'environment', key: 'environment', width: 110 },
  { title: 'Dropped At', dataIndex: 'dropped_at', key: 'dropped_at', width: 170 },
  {
    title: 'Storage Reclaimed', dataIndex: 'bytes_reclaimed', key: 'bytes_reclaimed', width: 150,
    render: (v: number | null) => (v ? `${(v / 1e9).toFixed(2)} GB` : '0 GB'),
  },
  {
    title: 'S3 Cleaned', dataIndex: 's3_cleaned', key: 's3_cleaned', width: 100,
    render: (v: boolean | null) => (v ? '✅' : '❌'),
  },
  {
    title: 'Catalog Dropped', dataIndex: 'catalog_dropped', key: 'catalog_dropped', width: 130,
    render: (v: boolean | null) => (v ? '✅' : '❌'),
  },
  { title: 'Previous State', dataIndex: 'previous_state', key: 'previous_state', width: 140 },
];

export function DeletionHistoryTab() {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(15);
  const deletions = useDeletionHistory(page, size);
  const rows = deletions.data?.data ?? [];
  const totalReclaimed = rows.reduce((sum, r) => sum + (r.bytes_reclaimed ?? 0), 0);

  const handleExport = () => {
    if (rows.length === 0) {
      message.info('No rows to export.');
      return;
    }
    downloadCsv(rows, 'zamboni_deletion_history.csv');
  };

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card size="small"><Statistic title="Tables Deleted (this page)" value={rows.length} /></Card></Col>
        <Col span={6}>
          <Card size="small"><Statistic title="Storage Reclaimed (this page)" value={(totalReclaimed / 1e9).toFixed(1)} suffix="GB" /></Card>
        </Col>
      </Row>
      <DataGrid<DeletionRow>
        columns={columns}
        queryResult={deletions}
        rowKey="table_fqn"
        onPageChange={(p, s) => { setPage(p); setSize(s); }}
        emptyText="No tables deleted recently."
        toolbar={
          <Button icon={<DownloadSimple size={14} />} onClick={handleExport}>
            Export CSV
          </Button>
        }
      />
    </div>
  );
}
