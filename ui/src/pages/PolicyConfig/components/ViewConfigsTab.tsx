import { Button, Col, Row, Select, Switch, Tag } from 'antd';
import { useState } from 'react';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { usePoliciesList } from '../../../api/hooks/usePolicies';
import type { PolicyViewRow } from '../../../api/types';
import { DataGrid } from '../../../components/DataGrid';
import { downloadCsv } from '../../../utils/csv';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];
const VALID_TIERS = ['critical', 'standard', 'low'];

const gateTag = (v: boolean | null) => (v ? <Tag color="green">✅</Tag> : <Tag color="default">❌</Tag>);
const statusTag = (v: boolean | null) => (v ? <Tag color="orange">⚠️ Override</Tag> : <Tag color="blue">✅ Template</Tag>);

const COLUMNS = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn' },
  { title: 'Domain', dataIndex: 'domain', key: 'domain' },
  { title: 'Layer', dataIndex: 'layer', key: 'layer' },
  { title: 'Tier', dataIndex: 'tier', key: 'tier' },
  { title: 'Template', dataIndex: 'policy_template', key: 'policy_template' },
  { title: 'Strategy', dataIndex: 'compaction_strategy', key: 'compaction_strategy' },
  { title: 'Engine', dataIndex: 'compaction_engine', key: 'compaction_engine' },
  { title: 'Target MB', dataIndex: 'compaction_target_file_size_mb', key: 'compaction_target_file_size_mb' },
  { title: 'Snap Days', dataIndex: 'snapshot_retention_days', key: 'snapshot_retention_days' },
  { title: 'Snap Floor', dataIndex: 'snapshot_min_to_keep', key: 'snapshot_min_to_keep' },
  { title: 'Orphan Days', dataIndex: 'orphan_file_retention_days', key: 'orphan_file_retention_days' },
  { title: 'Frequency', dataIndex: 'run_frequency', key: 'run_frequency' },
  { title: 'Status', dataIndex: 'manually_overridden', key: 'manually_overridden', render: statusTag },
  { title: 'Gate 1', dataIndex: 'gate1_enabled', key: 'gate1_enabled', render: gateTag },
  { title: 'Gate 2', dataIndex: 'gate2_enabled', key: 'gate2_enabled', render: gateTag },
  { title: 'Gate 3', dataIndex: 'gate3_enabled', key: 'gate3_enabled', render: gateTag },
];

/** View Configs tab (3_Policy_Configuration.py tab_view). */
export function ViewConfigsTab() {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(15);
  const [domain, setDomain] = useState<string | undefined>();
  const [layer, setLayer] = useState<string | undefined>();
  const [tier, setTier] = useState<string | undefined>();
  const [showAll, setShowAll] = useState(true);

  const domains = useDomainsList(true);
  const policies = usePoliciesList({ page, size, domain, layer, tier });

  const rows = (policies.data?.data ?? []).filter((r) => showAll || r.gate1_enabled || r.gate2_enabled || r.gate3_enabled);

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Select
            style={{ width: '100%' }} allowClear placeholder="Filter by Domain"
            value={domain} onChange={(v) => { setDomain(v); setPage(1); }}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          />
        </Col>
        <Col span={6}>
          <Select
            style={{ width: '100%' }} allowClear placeholder="Filter by Layer"
            value={layer} onChange={(v) => { setLayer(v); setPage(1); }}
            options={VALID_LAYERS.map((l) => ({ value: l, label: l }))}
          />
        </Col>
        <Col span={6}>
          <Select
            style={{ width: '100%' }} allowClear placeholder="Filter by Tier"
            value={tier} onChange={(v) => { setTier(v); setPage(1); }}
            options={VALID_TIERS.map((t) => ({ value: t, label: t }))}
          />
        </Col>
        <Col span={6}>
          <Switch checked={showAll} onChange={setShowAll} checkedChildren="Show all tables" unCheckedChildren="HK-enabled only" />
        </Col>
      </Row>

      <DataGrid<PolicyViewRow>
        columns={COLUMNS}
        queryResult={{ ...policies, data: policies.data ? { ...policies.data, data: rows } : undefined }}
        rowKey="table_fqn"
        onPageChange={(p, s) => { setPage(p); setSize(s); }}
        toolbar={
          <Button onClick={() => downloadCsv(rows, 'hk_config.csv')} disabled={rows.length === 0}>⬇️ Export CSV</Button>
        }
        emptyText="No tables match the selected filters."
      />
    </div>
  );
}
