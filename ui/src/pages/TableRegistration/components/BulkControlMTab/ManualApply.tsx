import { Button, Card, Col, Input, InputNumber, message, Row, Select, Table, Tag } from 'antd';
import dayjs from 'dayjs';
import { useState } from 'react';
import { useDomainsList } from '../../../../api/hooks/useDomains';
import { useBulkControlM, useTablesList } from '../../../../api/hooks/useTables';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];
const JOB_TYPE_OPTIONS = ['controlm', 'glue', 'lambda', 'step_functions', 'airflow', 'other'];

/**
 * Manual Bulk Apply (2_Table_Registration.py bc_tab_manual). The Streamlit
 * exclude-checkbox multiselect becomes native AntD rowSelection on the
 * preview grid -- deselecting a row is the exclude action, no separate
 * widget needed.
 */
export function ManualApply() {
  const [pipelineJob, setPipelineJob] = useState('');
  const [startTime, setStartTime] = useState('02:00');
  const [duration, setDuration] = useState(0);
  const [hkJob, setHkJob] = useState('');
  const [gate1Job, setGate1Job] = useState('');
  const [jobType, setJobType] = useState('controlm');
  const [ciNumber, setCiNumber] = useState('');
  const [streamId, setStreamId] = useState('');

  const [domain, setDomain] = useState<string | undefined>();
  const [layer, setLayer] = useState<string | undefined>();
  const [databaseName, setDatabaseName] = useState('');
  const [pattern, setPattern] = useState('');

  const [previewed, setPreviewed] = useState(false);
  const [selectedFqns, setSelectedFqns] = useState<string[]>([]);

  const domains = useDomainsList(true);
  const matches = useTablesList({
    // size is capped at 250 by api/deps.py::PageParams (>250 -> 422).
    page: 1, size: 250, domain, layer, database_name: databaseName || undefined, search: pattern || undefined,
  });
  const bulk = useBulkControlM();

  const matchedRows = previewed ? matches.data?.data ?? [] : [];
  const excludedCount = matchedRows.length - selectedFqns.length;

  const handlePreview = () => {
    setPreviewed(true);
    matches.refetch().then((r) => setSelectedFqns((r.data?.data ?? []).map((t) => t.table_fqn)));
  };

  const handleApply = () => {
    const setFields: Record<string, unknown> = {
      controlm_pipeline_job: pipelineJob.trim(),
      controlm_hk_job: hkJob.trim(),
      dependent_on_controlm_job: gate1Job.trim() || pipelineJob.trim(),
      dependent_job_type: jobType,
      controlm_job_start_time: startTime,
      controlm_expected_duration_min: duration,
    };
    if (ciNumber.trim()) setFields.ci_number = ciNumber.trim();
    if (streamId.trim()) setFields.stream_id = streamId.trim();

    const excludeFqns = matchedRows.filter((t) => !selectedFqns.includes(t.table_fqn)).map((t) => t.table_fqn);

    bulk.mutate(
      {
        filters: { domain, layer, database_name: databaseName || undefined, pattern: pattern || undefined, exclude_fqns: excludeFqns },
        set_fields: setFields,
        dry_run: false,
      },
      {
        onSuccess: (r) => {
          message.success(`✅ Applied to ${r.affected} table(s) (audit: ${r.audit_id}).`);
          setPreviewed(false);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Bulk apply failed.'),
      },
    );
  };

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Control-M Job Name *</div>
          <Input value={pipelineJob} onChange={(e) => setPipelineJob(e.target.value)} placeholder="ACE-DA-FIN-APS-INGEST-PRD" />
        </Col>
        <Col span={6}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job run start time</div>
          <Input
            value={startTime} onChange={(e) => setStartTime(e.target.value)} placeholder="02:00"
            onBlur={() => { if (!/^([01]?\d|2[0-3]):[0-5]\d$/.test(startTime)) setStartTime(dayjs().hour(2).minute(0).format('HH:mm')); }}
          />
        </Col>
        <Col span={6}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected duration (min)</div>
          <InputNumber min={0} max={480} style={{ width: '100%' }} value={duration} onChange={(v) => setDuration(v ?? 0)} />
        </Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>HK Control-M Job</div>
          <Input value={hkJob} onChange={(e) => setHkJob(e.target.value)} placeholder="ACE-DA-FIN-HK-PRD" />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>AWS Job Name — Gate 1 (optional)</div>
          <Input value={gate1Job} onChange={(e) => setGate1Job(e.target.value)} placeholder="Leave blank to use Control-M Job Name" />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Type</div>
          <Select style={{ width: '100%' }} value={jobType} onChange={setJobType} options={JOB_TYPE_OPTIONS.map((j) => ({ value: j, label: j }))} />
        </Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>CI Number (optional — blank keeps existing)</div>
          <Input value={ciNumber} onChange={(e) => setCiNumber(e.target.value)} placeholder="CI-10300" />
        </Col>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Stream ID (optional — blank keeps existing)</div>
          <Input value={streamId} onChange={(e) => setStreamId(e.target.value)} placeholder="STR-FIN-APS-0001" />
        </Col>
      </Row>

      <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>Apply to tables matching:</div>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={8}>
          <Select style={{ width: '100%' }} allowClear placeholder="Domain (All)" value={domain} onChange={setDomain} options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))} />
        </Col>
        <Col span={8}>
          <Select style={{ width: '100%' }} allowClear placeholder="Layer (All)" value={layer} onChange={setLayer} options={VALID_LAYERS.map((l) => ({ value: l, label: l }))} />
        </Col>
        <Col span={8}>
          <Input placeholder="Database (optional)" value={databaseName} onChange={(e) => setDatabaseName(e.target.value)} />
        </Col>
      </Row>
      <Input
        style={{ marginBottom: 12 }} placeholder="Table name pattern (optional) — aps_%_staging  or  %_ingest_%"
        value={pattern} onChange={(e) => setPattern(e.target.value)}
      />

      <Button onClick={handlePreview} disabled={!pipelineJob.trim()} style={{ marginBottom: 16 }}>
        🔍 Preview Matching Tables
      </Button>

      {previewed && (
        <Card size="small" title={`${matchedRows.length} table(s) matched`}>
          <Table
            size="small"
            loading={matches.isFetching}
            dataSource={matchedRows}
            rowKey="table_fqn"
            rowSelection={{ selectedRowKeys: selectedFqns, onChange: (keys) => setSelectedFqns(keys as string[]) }}
            columns={[
              { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn' },
              { title: 'Domain', dataIndex: 'domain', key: 'domain' },
              { title: 'Layer', dataIndex: 'layer', key: 'layer' },
              { title: 'Tier', dataIndex: 'tier', key: 'tier' },
            ]}
            pagination={matchedRows.length > 15 ? { pageSize: 15 } : false}
          />
          <div style={{ margin: '12px 0' }}>
            {excludedCount > 0 ? (
              <Tag color="gold">✅ {selectedFqns.length} will be updated · ⛔ {excludedCount} excluded</Tag>
            ) : (
              <Tag color="green">✅ All {selectedFqns.length} table(s) will be updated</Tag>
            )}
          </div>
          <Button type="primary" disabled={selectedFqns.length === 0} loading={bulk.isPending} onClick={handleApply}>
            🔗 Apply to {selectedFqns.length} Table(s)
          </Button>
        </Card>
      )}
    </div>
  );
}
