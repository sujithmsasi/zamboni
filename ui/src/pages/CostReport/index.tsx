import { Alert, Card, Col, Row, Select, Skeleton, Statistic, Table } from 'antd';
import { useState } from 'react';
import { PageHeader } from '../../components/PageHeader';
import { useCostReportData } from './hooks';

const GROUP_OPTIONS = [
  { value: 'domain', label: 'Domain' },
  { value: 'layer', label: 'Layer' },
  { value: 'tier', label: 'Tier' },
];

const PERIOD_OPTIONS = [
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 180, label: 'Last 6 months' },
];

// Per the phase brief: "prefer skipping charts this wave" unless
// @ant-design/plots is added (it isn't -- recharts already covers Home/
// Health Dashboard, and adding a second charting library for one page isn't
// worth it). Also not ported: the Streamlit twin's Budget Alert and ROI
// Estimate sections -- Budget depends on the Settings page (its own later
// wave) and ROI needs a second ad-hoc 30d query the endpoint doesn't expose
// independently of the selected period; deferred rather than fabricated.
export default function CostReportPage() {
  const [groupBy, setGroupBy] = useState('domain');
  const [days, setDays] = useState(30);
  const { costs } = useCostReportData(groupBy, days);

  if (costs.isError) {
    return (
      <Alert
        type="error" showIcon message="Failed to load cost report"
        description={costs.error instanceof Error ? costs.error.message : String(costs.error)}
      />
    );
  }

  const data = costs.data;
  const totals = data?.totals;

  const columns = [
    { title: GROUP_OPTIONS.find((g) => g.value === groupBy)?.label ?? groupBy, dataIndex: groupBy, key: groupBy },
    { title: 'Estimated Cost ($)', dataIndex: 'cost_usd', key: 'cost_usd' },
    { title: 'GB Scanned', dataIndex: 'gb_scanned', key: 'gb_scanned' },
  ];

  return (
    <div>
      <PageHeader title="Cost Report" subtitle="Athena scan costs and storage reclaimed — all figures are estimates" />

      {data && (
        <Alert
          style={{ marginBottom: 16 }}
          type={data.cost_explorer_enabled ? 'success' : 'info'}
          showIcon
          message={
            data.cost_explorer_enabled
              ? 'Live Billing Mode — cost data from AWS Cost Explorer (cached 24h).'
              : 'Estimate Mode — cost derived from bytes_scanned ($5/TB). Enable live billing in Settings.'
          }
        />
      )}

      <Card size="small" style={{ marginBottom: 16 }}>
        <Select style={{ width: 160, marginRight: 12 }} value={groupBy} onChange={setGroupBy} options={GROUP_OPTIONS} />
        <Select style={{ width: 160 }} value={days} onChange={setDays} options={PERIOD_OPTIONS} />
      </Card>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            {costs.isLoading ? <Skeleton.Input active /> : (
              <Statistic title="Estimated Athena Cost" prefix="$" value={totals?.estimated_athena_cost_usd ?? 0} />
            )}
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            {costs.isLoading ? <Skeleton.Input active /> : (
              <Statistic title="GB Scanned (Athena)" value={totals?.gb_scanned ?? 0} suffix="GB" />
            )}
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            {costs.isLoading ? <Skeleton.Input active /> : (
              <Statistic title="GB Compacted" value={totals?.gb_compacted ?? 0} suffix="GB" />
            )}
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            {costs.isLoading ? <Skeleton.Input active /> : (
              <Statistic title="GB Archived (Staging→IT)" value={totals?.gb_archived ?? 0} suffix="GB" />
            )}
          </Card>
        </Col>
      </Row>

      <Card size="small" title={`Athena Cost by ${GROUP_OPTIONS.find((g) => g.value === groupBy)?.label}`}>
        <Table
          size="small"
          loading={costs.isLoading}
          columns={columns}
          dataSource={data?.by_group ?? []}
          rowKey={groupBy}
          pagination={(data?.by_group.length ?? 0) > 15 ? { pageSize: 15 } : false}
        />
      </Card>
    </div>
  );
}
