import { Modal, Table } from 'antd';
import { Link } from 'react-router-dom';
import type { CoverageByDomain } from '../../../api/types';
import { CoverageByDomainChart } from './CoverageByDomainChart';

interface CoverageDetailModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  data: CoverageByDomain[];
}

const COLUMNS = [
  { title: 'Domain', dataIndex: 'domain', key: 'domain' },
  { title: 'Total Tables', dataIndex: 'total', key: 'total', align: 'right' as const },
  { title: 'HK Enabled', dataIndex: 'enabled', key: 'enabled', align: 'right' as const },
  {
    title: 'Coverage',
    dataIndex: 'pct_enabled',
    key: 'pct_enabled',
    align: 'right' as const,
    render: (v: number) => `${v}%`,
  },
];

/** Drill-down for the Tables Registered / HK Enabled / Coverage KPI cards. */
export function CoverageDetailModal({ open, onClose, title, data }: CoverageDetailModalProps) {
  return (
    <Modal open={open} onCancel={onClose} onOk={onClose} title={title} width={640} footer={null}>
      <CoverageByDomainChart data={data} />
      <Table
        style={{ marginTop: 16 }}
        size="small"
        columns={COLUMNS}
        dataSource={data}
        rowKey="domain"
        pagination={false}
      />
      <div style={{ marginTop: 16, textAlign: 'right' }}>
        <Link to="/health" onClick={onClose} style={{ fontSize: 13 }}>
          Open full Health Dashboard →
        </Link>
      </div>
    </Modal>
  );
}
