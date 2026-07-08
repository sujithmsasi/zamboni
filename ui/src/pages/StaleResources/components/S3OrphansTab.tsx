import { Alert, Button, Card, Input, message, Statistic, Table } from 'antd';
import { useState } from 'react';
import { useStaleOrphan } from '../../../api/hooks/useStale';
import type { StaleOrphanRow } from '../../../api/types';

const columns = [
  { title: 'S3 Prefix', dataIndex: 's3_prefix', key: 's3_prefix', ellipsis: true },
  { title: 'Status', dataIndex: 'status', key: 'status' },
];

/** S3 Orphans tab (10_Stale_Resources.py tab3) -- lists S3 sub-prefixes not
 * cross-referenced against Glue. Full CloudTrail-backed orphan detection
 * remains a documented Phase 2+ follow-up, same caveat the twin itself carries. */
export function S3OrphansTab() {
  const [prefix, setPrefix] = useState('');
  const [scanTarget, setScanTarget] = useState<string | null>(null);

  const scan = useStaleOrphan(scanTarget ?? '', !!scanTarget);
  const rows = scan.data ?? [];

  const handleScan = () => {
    if (!prefix.trim().startsWith('s3://')) {
      message.error('Enter a valid s3:// prefix.');
      return;
    }
    setScanTarget(prefix.trim());
  };

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="S3 prefixes that contain data but have no corresponding Glue table."
        description="These are typically leftover from dropped tables where the S3 data was not cleaned up. Full orphan detection requires CloudTrail integration (Phase 2 feature) -- results below need manual cross-reference with Glue."
      />
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>S3 Base Prefix to Scan</div>
          <Input
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            placeholder="s3://your-staging-bucket/staging/finance/"
          />
        </div>
        <Button type="primary" disabled={!prefix} loading={scan.isFetching} onClick={handleScan}>
          🔍 Scan S3
        </Button>
      </div>

      {scan.isError && (
        <Alert type="error" showIcon message="S3 scan failed" description={scan.error instanceof Error ? scan.error.message : String(scan.error)} style={{ marginBottom: 16 }} />
      )}

      {scanTarget && !scan.isFetching && !scan.isError && (
        <>
          <Card size="small" style={{ marginBottom: 16, width: 220 }}>
            <Statistic title="S3 Sub-prefixes Found" value={rows.length} />
          </Card>
          {rows.length === 0 && <Alert type="info" showIcon message="No sub-prefixes found. Try a higher-level prefix." />}
        </>
      )}

      {rows.length > 0 && (
        <Table<StaleOrphanRow> size="small" loading={scan.isFetching} columns={columns} dataSource={rows} rowKey="s3_prefix" pagination={{ pageSize: 15 }} />
      )}
      {!scanTarget && <div style={{ color: '#667085', fontSize: 13 }}>Enter a prefix above, then click Scan.</div>}
    </div>
  );
}
