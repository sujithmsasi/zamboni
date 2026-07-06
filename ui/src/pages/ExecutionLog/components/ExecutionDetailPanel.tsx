import { Alert, Descriptions, Skeleton } from 'antd';
import { StateBadge } from '../../../components/StateBadge';
import { useExecutionDetail } from '../hooks';

interface ExecutionDetailPanelProps {
  id: string;
}

// Rendered lazily -- only mounted when its row is expanded, so
// useExecutionDetail's query doesn't fire until the user actually opens it.
export function ExecutionDetailPanel({ id }: ExecutionDetailPanelProps) {
  const { data, isLoading } = useExecutionDetail(id);

  if (isLoading) return <Skeleton active paragraph={{ rows: 2 }} />;
  if (!data) return null;

  return (
    <div style={{ padding: '4px 12px 12px' }}>
      <Descriptions size="small" column={3} bordered>
        <Descriptions.Item label="Integrity Status">
          <StateBadge state={data.integrity_status} />
        </Descriptions.Item>
        <Descriptions.Item label="Lock ID">{String(data.lock_id ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Dry Run">{data.dry_run ? 'Yes' : 'No'}</Descriptions.Item>
        <Descriptions.Item label="Metadata Before">{String(data.metadata_location_before ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Metadata After">{String(data.metadata_location_after ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Snapshot Before">{String(data.snapshot_id_before ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Snapshot After">{String(data.snapshot_id_after ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Skip Reason">{String(data.skip_reason ?? '—')}</Descriptions.Item>
      </Descriptions>
      {data.error_message ? (
        <Alert style={{ marginTop: 8 }} type="error" showIcon message={String(data.error_message)} />
      ) : null}
    </div>
  );
}
