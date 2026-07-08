import { Alert, Button, Col, InputNumber, Row, Select, Table } from 'antd';
import { useState } from 'react';
import { useStaleZeroRow } from '../../../api/hooks/useStale';
import type { StaleZeroRowRow } from '../../../api/types';
import { useStaleResourcesData } from '../hooks';

const columns = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 120 },
  { title: 'Layer', dataIndex: 'layer', key: 'layer', width: 120 },
  { title: 'Last Rows Archived', dataIndex: 'last_rows_archived', key: 'last_rows_archived', width: 160 },
  { title: 'Last Partition Archived', dataIndex: 'last_partition_archived', key: 'last_partition_archived', width: 180 },
];

/** Zero-Row tab (10_Stale_Resources.py tab4) -- registered tables whose most
 * recent archival run exported few or no rows, candidates for deregistration. */
export function ZeroRowTab() {
  const [domain, setDomain] = useState<string | undefined>();
  const [threshold, setThreshold] = useState(0);
  const [run, setRun] = useState(false);

  const { domains } = useStaleResourcesData();
  const query = useStaleZeroRow(domain, threshold, run);
  const rows = query.data ?? [];

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Tables registered in Zamboni that appear to have very few or no recent records."
        description="These are candidates for review — they may be safe to deregister or archive entirely."
      />
      <Row gutter={16} style={{ marginBottom: 16 }} align="bottom">
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain</div>
          <Select
            style={{ width: '100%' }} allowClear placeholder="All"
            value={domain} onChange={setDomain}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Max row count threshold</div>
          <InputNumber style={{ width: '100%' }} min={0} value={threshold} onChange={(v) => setThreshold(v ?? 0)} />
        </Col>
        <Col span={8}>
          <Button type="primary" loading={query.isFetching} onClick={() => setRun(true)}>
            🔍 Find Low-Row Tables
          </Button>
        </Col>
      </Row>

      {run && !query.isFetching && rows.length === 0 && (
        <Alert type="success" showIcon message="No low-row tables found with the selected threshold." />
      )}
      {run && !query.isFetching && rows.length > 0 && (
        <>
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`${rows.length} tables archived with ≤ ${threshold} rows`} />
          <Table<StaleZeroRowRow> size="small" loading={query.isFetching} columns={columns} dataSource={rows} rowKey="table_fqn" pagination={{ pageSize: 15 }} />
        </>
      )}
    </div>
  );
}
