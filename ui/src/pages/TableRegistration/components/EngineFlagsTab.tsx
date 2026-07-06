import { Alert, Button, Card, Checkbox, Col, Input, message, Row, Select, Tabs } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useBulkControlM, useTableDetail, useTablesSearch, useUpdateTable } from '../../../api/hooks/useTables';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];

function SingleTableFlags() {
  const [fqn, setFqn] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [hk, setHk] = useState(false);
  const [archive, setArchive] = useState(false);
  const [lifecycle, setLifecycle] = useState(false);

  const tables = useTablesSearch(search);
  const detail = useTableDetail(fqn);
  const update = useUpdateTable();

  // Keyed on fqn, not detail.data's identity -- see the note in
  // PolicyConfig/components/EditTableTab.tsx for why.
  const appliedForFqn = useRef<string | null>(null);
  useEffect(() => {
    if (!detail.data || appliedForFqn.current === fqn) return;
    appliedForFqn.current = fqn;
    setHk(!!detail.data.hk_enabled);
    setArchive(!!detail.data.archive_enabled);
    setLifecycle(!!detail.data.lifecycle_enabled);
  }, [detail.data, fqn]);

  const handleSave = () => {
    if (!fqn) return;
    update.mutate(
      { fqn, body: { hk_enabled: hk, archive_enabled: archive, lifecycle_enabled: lifecycle, dry_run: false } },
      {
        onSuccess: (r) => message.success(`✅ Flags updated for ${fqn} (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Failed.'),
      },
    );
  };

  return (
    <div>
      <Select
        showSearch
        style={{ width: 420, marginBottom: 16 }}
        placeholder="Table (type to search)"
        value={fqn}
        onChange={setFqn}
        onSearch={setSearch}
        filterOption={false}
        options={(tables.data?.data ?? []).map((t) => ({ value: t.table_fqn, label: t.table_fqn }))}
        notFoundContent={tables.isLoading ? 'Searching…' : 'Type to search'}
      />
      {fqn && detail.data && (
        <Card size="small">
          <Row gutter={24} style={{ marginBottom: 16 }}>
            <Col span={8}><Checkbox checked={hk} onChange={(e) => setHk(e.target.checked)}>🔧 Housekeeping Enabled</Checkbox></Col>
            <Col span={8}><Checkbox checked={archive} onChange={(e) => setArchive(e.target.checked)}>📦 Archival Enabled</Checkbox></Col>
            <Col span={8}><Checkbox checked={lifecycle} onChange={(e) => setLifecycle(e.target.checked)}>♻️ Lifecycle Enabled</Checkbox></Col>
          </Row>
          <Button type="primary" onClick={handleSave} loading={update.isPending}>💾 Save Flags</Button>
        </Card>
      )}
    </div>
  );
}

type FlagChoice = 'no_change' | 'enable' | 'disable';
const FLAG_OPTIONS: { value: FlagChoice; label: string }[] = [
  { value: 'no_change', label: 'no change' },
  { value: 'enable', label: 'enable' },
  { value: 'disable', label: 'disable' },
];

function BulkApplyFlags() {
  const [domain, setDomain] = useState<string | undefined>();
  const [layer, setLayer] = useState<string | undefined>();
  const [databaseName, setDatabaseName] = useState('');
  const [hk, setHk] = useState<FlagChoice>('no_change');
  const [archive, setArchive] = useState<FlagChoice>('no_change');
  const [lifecycle, setLifecycle] = useState<FlagChoice>('no_change');

  const domains = useDomainsList(true);
  const bulk = useBulkControlM();

  const allNoChange = hk === 'no_change' && archive === 'no_change' && lifecycle === 'no_change';

  const setFields: Record<string, boolean> = {};
  if (hk !== 'no_change') setFields.hk_enabled = hk === 'enable';
  if (archive !== 'no_change') setFields.archive_enabled = archive === 'enable';
  if (lifecycle !== 'no_change') setFields.lifecycle_enabled = lifecycle === 'enable';

  const handleApply = () => {
    bulk.mutate(
      {
        filters: { domain, layer, database_name: databaseName || undefined },
        set_fields: setFields,
        dry_run: false,
      },
      {
        onSuccess: (r) => message.success(`✅ Flags updated for ${r.affected} table(s) (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Bulk update failed.'),
      },
    );
  };

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Apply engine flag settings to all tables in a domain or database. Useful for onboarding a new domain or disabling archival for a whole layer."
      />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain</div>
          <Select
            style={{ width: '100%' }} allowClear placeholder="All"
            value={domain} onChange={setDomain}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Layer</div>
          <Select
            style={{ width: '100%' }} allowClear placeholder="All"
            value={layer} onChange={setLayer}
            options={VALID_LAYERS.map((l) => ({ value: l, label: l }))}
          />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Database (optional)</div>
          <Input value={databaseName} onChange={(e) => setDatabaseName(e.target.value)} placeholder="finance_staging_db" />
        </Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>🔧 Housekeeping</div>
          <Select style={{ width: '100%' }} value={hk} onChange={setHk} options={FLAG_OPTIONS} />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>📦 Archival</div>
          <Select style={{ width: '100%' }} value={archive} onChange={setArchive} options={FLAG_OPTIONS} />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>♻️ Lifecycle</div>
          <Select style={{ width: '100%' }} value={lifecycle} onChange={setLifecycle} options={FLAG_OPTIONS} />
        </Col>
      </Row>
      <Button type="primary" disabled={allNoChange} loading={bulk.isPending} onClick={handleApply}>
        ⚙️ Apply Bulk Flags
      </Button>
    </div>
  );
}

/** Engine Flags tab (2_Table_Registration.py tab_engine_flags) -- present in the app though not called out by name in the phase brief's tab list. */
export function EngineFlagsTab() {
  return (
    <Tabs
      items={[
        { key: 'single', label: 'Single Table', children: <SingleTableFlags /> },
        { key: 'bulk', label: 'Bulk Apply', children: <BulkApplyFlags /> },
      ]}
    />
  );
}
