import { Table } from 'antd';
import { StateBadge } from '../../../components/StateBadge';
import type { FlaggedTable } from '../../../api/types';

interface UnhealthyTablesGridProps {
  data: FlaggedTable[];
}

const COLUMNS = [
  {
    title: 'Status',
    dataIndex: 'status',
    key: 'status',
    width: 130,
    render: (status: string) => <StateBadge state={status} />,
  },
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 110 },
  { title: 'Tier', dataIndex: 'tier', key: 'tier', width: 90 },
  { title: 'Reason', dataIndex: 'reason', key: 'reason' },
];

// Plain AntD Table, not the shared <DataGrid> -- this is a fixed array
// embedded in the health_kpis payload, not a server-paginated resource, so
// DataGrid's query-result/pagination contract doesn't fit (see PATTERN.md).
export function UnhealthyTablesGrid({ data }: UnhealthyTablesGridProps) {
  return (
    <Table
      size="small"
      columns={COLUMNS}
      dataSource={data}
      rowKey="table_fqn"
      pagination={data.length > 10 ? { pageSize: 10, size: 'small' } : false}
    />
  );
}
