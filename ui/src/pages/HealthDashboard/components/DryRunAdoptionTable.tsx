import { Table } from 'antd';
import { StateBadge } from '../../../components/StateBadge';
import type { DryRunAdoptionRow } from '../../../api/types';

interface DryRunAdoptionTableProps {
  data: DryRunAdoptionRow[];
}

const COLUMNS = [
  { title: 'Domain', dataIndex: 'domain', key: 'domain' },
  { title: 'Tables in Dry-Run', dataIndex: 'tables_in_dry_run', key: 'tables_in_dry_run', align: 'right' as const },
  {
    title: 'Longest Waiting',
    dataIndex: 'max_days_waiting',
    key: 'max_days_waiting',
    align: 'right' as const,
    render: (v: number) => `${v}d`,
  },
  {
    title: '',
    dataIndex: 'stale',
    key: 'stale',
    width: 110,
    render: (stale: boolean) => <StateBadge state={stale ? 'STALE_CANDIDATE' : 'ACTIVE'} />,
  },
];

/**
 * Not a historical trend (no "graduated_at" event is captured anywhere in
 * the schema) -- which domains have tables stuck longest in shadow mode,
 * answering "which teams are hesitant to turn HK on" directly instead.
 */
export function DryRunAdoptionTable({ data }: DryRunAdoptionTableProps) {
  return <Table size="small" columns={COLUMNS} dataSource={data} rowKey="domain" pagination={false} />;
}
