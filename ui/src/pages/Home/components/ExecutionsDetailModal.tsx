import { Modal } from 'antd';
import { Link } from 'react-router-dom';
import type { ExecutionRow } from '../../../api/types';
import type { Paged } from '../../../api/client';
import { DataGrid } from '../../../components/DataGrid';
import { StateBadge } from '../../../components/StateBadge';

interface ExecutionsDetailModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  queryResult: {
    data: Paged<ExecutionRow[]> | undefined;
    isLoading: boolean;
    isError: boolean;
    error?: unknown;
    refetch: () => void;
  };
  emptyText: string;
}

const COLUMNS = [
  { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 170 },
  { title: 'Engine', dataIndex: 'engine', key: 'engine', width: 90 },
  { title: 'Operation', dataIndex: 'operation', key: 'operation', width: 120 },
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  {
    title: 'Status',
    dataIndex: 'status',
    key: 'status',
    width: 110,
    render: (status: string) => <StateBadge state={status} />,
  },
];

/** Drill-down for the Executions Today / Failures (7d) KPI cards. */
export function ExecutionsDetailModal({ open, onClose, title, queryResult, emptyText }: ExecutionsDetailModalProps) {
  return (
    <Modal open={open} onCancel={onClose} onOk={onClose} title={title} width={800} footer={null}>
      <DataGrid<ExecutionRow>
        columns={COLUMNS}
        queryResult={queryResult}
        rowKey="execution_id"
        emptyText={emptyText}
      />
      <div style={{ marginTop: 12, textAlign: 'right' }}>
        <Link to="/executions" onClick={onClose} style={{ fontSize: 13 }}>
          Open full Execution Log →
        </Link>
      </div>
    </Modal>
  );
}
