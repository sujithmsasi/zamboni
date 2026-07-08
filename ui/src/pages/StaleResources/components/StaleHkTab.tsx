import { DownloadSimple } from '@phosphor-icons/react';
import { Alert, Button, Card, Col, InputNumber, message, Row, Select, Statistic, Table } from 'antd';
import { useState } from 'react';
import { useStaleHk } from '../../../api/hooks/useStale';
import type { StaleHkRow } from '../../../api/types';
import { downloadCsv } from '../../../utils/csv';
import { STALE_ENVIRONMENTS, useStaleResourcesData } from '../hooks';

const columns = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 120 },
  { title: 'Layer', dataIndex: 'layer', key: 'layer', width: 100 },
  { title: 'Tier', dataIndex: 'tier', key: 'tier', width: 100 },
  {
    title: 'HK Enabled', dataIndex: 'hk_enabled', key: 'hk_enabled', width: 100,
    render: (v: boolean | number | null) => (v ? '✅' : '❌'),
  },
  {
    title: 'Days Since HK', dataIndex: 'days_since_hk', key: 'days_since_hk', width: 130,
    render: (v: number | null) => (v === null || v === undefined ? 'Never' : `${v}d`),
  },
  { title: 'Last Successful HK', dataIndex: 'last_successful_hk', key: 'last_successful_hk', width: 170 },
];

/** Stale HK tab (10_Stale_Resources.py tab1's prod-stale half; the NonProd
 * Stale sub-section was promoted to its own tab per the phase brief's tab list). */
export function StaleHkTab() {
  const [domain, setDomain] = useState<string | undefined>();
  const [environment, setEnvironment] = useState('prod');
  const [days, setDays] = useState(30);

  const { domains } = useStaleResourcesData();
  const stale = useStaleHk(domain, environment, days);
  const rows = stale.data ?? [];

  const neverHk = rows.filter((r) => r.last_successful_hk == null).length;
  const hkDisabled = rows.filter((r) => !r.hk_enabled).length;

  const handleExport = () => {
    if (rows.length === 0) {
      message.info('No rows to export.');
      return;
    }
    downloadCsv(rows, 'stale_tables.csv');
  };

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain</div>
          <Select
            style={{ width: '100%' }} allowClear placeholder="All"
            value={domain} onChange={setDomain}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          />
        </Col>
        <Col span={6}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Environment</div>
          <Select
            style={{ width: '100%' }} value={environment} onChange={setEnvironment}
            options={STALE_ENVIRONMENTS.map((e) => ({ value: e, label: e }))}
          />
        </Col>
        <Col span={6}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Inactive for more than (days)</div>
          <InputNumber style={{ width: '100%' }} min={1} value={days} onChange={(v) => setDays(v ?? 30)} />
        </Col>
      </Row>

      {!stale.isLoading && rows.length === 0 && (
        <Alert type="success" showIcon message={`No stale tables found (threshold: ${days} days).`} style={{ marginBottom: 16 }} />
      )}
      {!stale.isLoading && rows.length > 0 && (
        <>
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`${rows.length} tables have had no successful HK in ${days}+ days`} />
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}><Card size="small"><Statistic title="Stale Tables" value={rows.length} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="Never Housekept" value={neverHk} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="HK Disabled" value={hkDisabled} /></Card></Col>
          </Row>
        </>
      )}

      <div style={{ marginBottom: 12 }}>
        <Button icon={<DownloadSimple size={14} />} onClick={handleExport}>Export CSV</Button>
      </div>
      <Table<StaleHkRow>
        size="small"
        loading={stale.isLoading}
        columns={columns}
        dataSource={rows}
        rowKey="table_fqn"
        pagination={rows.length > 15 ? { pageSize: 15, showSizeChanger: true, pageSizeOptions: [15, 25, 50, 100] } : false}
      />
    </div>
  );
}
