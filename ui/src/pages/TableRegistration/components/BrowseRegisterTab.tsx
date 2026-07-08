import { useQueries } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Progress,
  Row,
  Select,
  Statistic,
  Switch,
  Table,
  Tag,
} from 'antd';
import { useMemo, useState } from 'react';
import { request } from '../../../api/client';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useGlueDatabases, useGlueTables, useRegisterTable } from '../../../api/hooks/useTables';
import type { GlueTableRow } from '../../../api/types';
import { ControlMFields } from '../../../components/ControlMFields';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];
const VALID_TIERS = ['critical', 'standard', 'low'];

interface RowResult {
  fqn: string;
  ok: boolean;
  template?: string;
  error?: string;
}

/**
 * Browse & Register tab (2_Table_Registration.py tab_browse). "All Databases"
 * fans out GET /api/glue/tables/{db} across every database in parallel via
 * useQueries -- the Streamlit version did the same client-side aggregation,
 * just serially inside one page render.
 *
 * Not reimplemented client-side: the "auto-inferred template" preview shown
 * before submit in Streamlit (infer_template(layer, tier) is engine logic --
 * duplicating its layer/tier -> template table in TS risks silent drift from
 * the real thing). Instead, POST /api/tables/register now returns the
 * template it actually applied (api/services/tables_svc.py), and this page
 * shows it per-row in the result list after registering, which is the
 * authoritative value rather than a guess.
 */
export function BrowseRegisterTab() {
  const [selectedDb, setSelectedDb] = useState<string | null>(null);
  const [unregisteredOnly, setUnregisteredOnly] = useState(false);
  const [pattern, setPattern] = useState('');
  const [selectedFqns, setSelectedFqns] = useState<string[]>([]);
  const [results, setResults] = useState<RowResult[] | null>(null);
  const [progress, setProgress] = useState(0);
  const [form] = Form.useForm();

  const databases = useGlueDatabases();
  const domains = useDomainsList(true);
  const register = useRegisterTable();

  const singleDb = useGlueTables(selectedDb && selectedDb !== 'ALL' ? selectedDb : null, pattern, unregisteredOnly);

  const allDbList = useMemo(
    () => (selectedDb === 'ALL' ? databases.data ?? [] : []),
    [selectedDb, databases.data],
  );
  const allDbResults = useQueries({
    queries: allDbList.map((db) => ({
      queryKey: ['glue', 'tables', db, pattern, unregisteredOnly],
      queryFn: () =>
        request<GlueTableRow[]>(
          `/glue/tables/${db}?${new URLSearchParams({ pattern, unregistered_only: String(unregisteredOnly) })}`,
        ),
      enabled: selectedDb === 'ALL' && allDbList.length > 0,
    })),
  });

  const tables: (GlueTableRow & { database: string })[] = useMemo(() => {
    if (selectedDb === 'ALL') {
      return allDbList.flatMap((db, i) => (allDbResults[i]?.data ?? []).map((t) => ({ ...t, database: db })));
    }
    if (selectedDb) {
      return (singleDb.data ?? []).map((t) => ({ ...t, database: selectedDb }));
    }
    return [];
  }, [selectedDb, allDbList, allDbResults, singleDb.data]);

  const isLoading = selectedDb === 'ALL' ? allDbResults.some((r) => r.isLoading) : singleDb.isLoading;

  const iceberg = tables.filter((t) => t.format === 'iceberg').length;
  const alreadyRegistered = tables.filter((t) => t.registered).length;

  const selectedRows = tables.filter((t) => selectedFqns.includes(t.table_fqn));
  const newCount = selectedRows.filter((r) => !r.registered).length;
  const alreadyCount = selectedRows.length - newCount;

  const handleSubmit = async () => {
    const values = await form.validateFields();
    setResults(null);
    setProgress(0);
    const rows: RowResult[] = [];
    for (let i = 0; i < selectedRows.length; i++) {
      const row = selectedRows[i];
      try {
        const res = await register.mutateAsync({
          table_fqn: row.table_fqn,
          domain: values.domain,
          layer: values.layer,
          tier: values.tier,
          environment: 'prod',
          table_format: row.format,
          owner_email: values.owner_email || '',
          ci_number: values.ci_number || '',
          notes: values.notes || '',
          controlm_pipeline_job: values.controlm_pipeline_job || null,
          controlm_hk_job: values.controlm_hk_job || null,
          dependent_on_controlm_job: values.dependent_on_controlm_job || values.controlm_pipeline_job || null,
          controlm_job_start_time: values.controlm_job_start_time || '02:00',
          controlm_expected_duration_min: values.controlm_expected_duration_min ?? 0,
          dependent_job_type: values.dependent_job_type || 'controlm',
          dry_run: false,
        });
        rows.push({ fqn: row.table_fqn, ok: true, template: res.template });
      } catch (err) {
        rows.push({ fqn: row.table_fqn, ok: false, error: err instanceof Error ? err.message : String(err) });
      }
      setProgress(Math.round(((i + 1) / selectedRows.length) * 100));
    }
    setResults(rows);
    setSelectedFqns([]);
    // Streamlit's register form resets to its placeholder defaults on every
    // rerun (no session_state binding); this form stays mounted across
    // selections, so it must reset explicitly or Domain/Layer/Tier would
    // silently carry over -- and pass required-field validation -- for the
    // next, unrelated batch of tables.
    form.resetFields();
  };

  const columns = [
    { title: 'Table Name', dataIndex: 'name', key: 'name' },
    { title: 'Database', dataIndex: 'database', key: 'database' },
    { title: 'Format', dataIndex: 'format', key: 'format', width: 90 },
    {
      title: 'Registered', dataIndex: 'registered', key: 'registered', width: 100,
      render: (v: boolean) => (v ? <Tag color="green">✅</Tag> : <Tag>—</Tag>),
    },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Glue Database</div>
          <Select
            style={{ width: '100%' }}
            placeholder="Select a database"
            value={selectedDb}
            onChange={(v) => { setSelectedDb(v); setSelectedFqns([]); }}
            options={[
              { value: 'ALL', label: '-- All Databases --' },
              ...(databases.data ?? []).map((d) => ({ value: d, label: d })),
            ]}
            loading={databases.isLoading}
          />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Filter by name pattern</div>
          <Input
            placeholder="aps_%  or  %_staging  or  fin_aps"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
          />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>&nbsp;</div>
          <Switch
            checked={unregisteredOnly}
            onChange={setUnregisteredOnly}
            checkedChildren="Unregistered only" unCheckedChildren="Show all tables"
          />
        </Col>
      </Row>

      {selectedDb && (
        <>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}><Card size="small"><Statistic title="Shown" value={tables.length} /></Card></Col>
            <Col span={8}><Card size="small"><Statistic title="Iceberg" value={iceberg} /></Card></Col>
            <Col span={8}><Card size="small"><Statistic title="Already Registered" value={alreadyRegistered} /></Card></Col>
          </Row>

          <Table
            size="small"
            loading={isLoading}
            columns={columns}
            dataSource={tables}
            rowKey="table_fqn"
            rowSelection={{ selectedRowKeys: selectedFqns, onChange: (keys) => setSelectedFqns(keys as string[]) }}
            pagination={tables.length > 20 ? { pageSize: 20 } : false}
            style={{ marginBottom: 16 }}
          />

          {selectedRows.length > 0 && (
            <Card
              size="small"
              title={`${selectedRows.length} selected (${newCount} new · ${alreadyCount} already registered)`}
            >
              {alreadyCount > 0 && (
                <Alert
                  style={{ marginBottom: 12 }} type="warning" showIcon
                  message={`${alreadyCount} selected table(s) are already registered. They will be updated with the settings below.`}
                />
              )}
              <Form form={form} layout="vertical">
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="domain" label="Domain" rules={[{ required: true, message: 'Domain is required' }]}>
                      <Select
                        placeholder="— select domain —"
                        options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="layer" label="Layer" rules={[{ required: true, message: 'Layer is required' }]}>
                      <Select placeholder="— select layer —" options={VALID_LAYERS.map((l) => ({ value: l, label: l }))} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="tier" label="Tier" rules={[{ required: true, message: 'Tier is required' }]}>
                      <Select placeholder="— select tier —" options={VALID_TIERS.map((t) => ({ value: t, label: t }))} />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item name="owner_email" label="Owner Email"><Input /></Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name="ci_number" label="CI Number"><Input /></Form.Item>
                  </Col>
                </Row>
                <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>🔗 Control-M Integration (optional)</div>
                <ControlMFields />
                <Form.Item name="notes" label="Notes"><Input.TextArea rows={2} /></Form.Item>
                <Button type="primary" onClick={handleSubmit} loading={register.isPending}>
                  📥 Register Selected Tables
                </Button>
              </Form>
            </Card>
          )}

          {register.isPending && <Progress percent={progress} style={{ marginTop: 16 }} />}

          {results && (
            <Card size="small" title="Registration results" style={{ marginTop: 16 }}>
              {results.map((r) => (
                <div key={r.fqn} style={{ marginBottom: 4 }}>
                  {r.ok ? (
                    <Tag color="green">✅ {r.fqn} — template {r.template}</Tag>
                  ) : (
                    <Tag color="red">❌ {r.fqn} — {r.error}</Tag>
                  )}
                </div>
              ))}
            </Card>
          )}
        </>
      )}
    </div>
  );
}
