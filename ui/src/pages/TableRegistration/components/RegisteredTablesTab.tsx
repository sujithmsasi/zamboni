import { Button, Col, Row, Select, Switch, Tag } from 'antd';
import { useState } from 'react';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useTablesList } from '../../../api/hooks/useTables';
import type { TableRow } from '../../../api/types';
import { DataGrid } from '../../../components/DataGrid';
import { downloadCsv } from '../../../utils/csv';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];
const VALID_TIERS = ['critical', 'standard', 'low'];

const yesNo = (v: boolean) => (v ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>);

const COLUMNS = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn' },
  { title: 'Domain', dataIndex: 'domain', key: 'domain' },
  { title: 'Layer', dataIndex: 'layer', key: 'layer' },
  { title: 'Tier', dataIndex: 'tier', key: 'tier' },
  { title: 'CI', dataIndex: 'ci_number', key: 'ci_number' },
  { title: 'Owner', dataIndex: 'owner_email', key: 'owner_email' },
  { title: 'Control-M Job', dataIndex: 'controlm_pipeline_job', key: 'controlm_pipeline_job' },
  { title: 'HK ControlM Job', dataIndex: 'controlm_hk_job', key: 'controlm_hk_job' },
  { title: 'AWS Gate 1 Job', dataIndex: 'dependent_on_controlm_job', key: 'dependent_on_controlm_job' },
  { title: 'Job Start', dataIndex: 'controlm_job_start_time', key: 'controlm_job_start_time' },
  { title: 'HK', dataIndex: 'hk_enabled', key: 'hk_enabled', render: yesNo },
  { title: 'Archive', dataIndex: 'archive_enabled', key: 'archive_enabled', render: yesNo },
  { title: 'Lifecycle', dataIndex: 'lifecycle_enabled', key: 'lifecycle_enabled', render: yesNo },
  { title: 'Cadence', dataIndex: 'processing_cadence', key: 'processing_cadence' },
];

/**
 * Registered Tables tab (2_Table_Registration.py tab_registered). Per-table
 * gate state isn't shown here -- GET /api/tables is stream_registry-only (no
 * hk_config join, per contracts.md §6); gates already have a dedicated home
 * in Policy Configuration's View Configs tab, so this doesn't duplicate them.
 */
export function RegisteredTablesTab() {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(15);
  const [domain, setDomain] = useState<string | undefined>();
  const [layer, setLayer] = useState<string | undefined>();
  const [tier, setTier] = useState<string | undefined>();
  const [hkOnly, setHkOnly] = useState(false);

  const domains = useDomainsList(true);
  const tables = useTablesList({ page, size, domain, layer, tier });

  const rows = (tables.data?.data ?? []).filter((r) => !hkOnly || r.hk_enabled);

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
          <Switch checked={hkOnly} onChange={setHkOnly} checkedChildren="HK-enabled only" unCheckedChildren="All tables" />
        </Col>
      </Row>

      <DataGrid<TableRow>
        columns={COLUMNS}
        queryResult={{ ...tables, data: tables.data ? { ...tables.data, data: rows } : undefined }}
        rowKey="table_fqn"
        onPageChange={(p, s) => { setPage(p); setSize(s); }}
        toolbar={
          <Button onClick={() => downloadCsv(rows, `zamboni_tables_${domain ?? 'all'}.csv`)} disabled={rows.length === 0}>
            ⬇️ Export CSV
          </Button>
        }
        emptyText="No registered tables match your filters."
      />
    </div>
  );
}
