import { Alert, AutoComplete, Button, Card, Col, Input, InputNumber, message, Modal, Row, Select, Table } from 'antd';
import dayjs from 'dayjs';
import { useMemo, useState } from 'react';
import { useJobs } from '../../../api/hooks/useControlm';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useBulkControlM, useTablesList } from '../../../api/hooks/useTables';
import type { TableRow } from '../../../api/types';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];
const JOB_TYPE_OPTIONS = ['controlm', 'glue', 'lambda', 'step_functions', 'airflow', 'other'];
const JOB_FREQUENCY_OPTIONS = ['hourly', 'daily', 'weekly', 'monthly', 'every_trigger'];

/**
 * Manual Bulk Apply (2_Table_Registration.py bc_tab_manual). Target Tables
 * comes first -- confirm scope via a popup preview before filling in job
 * details, rather than the reverse (typing a job name only to discover the
 * filter matched nothing, or the wrong tables). The preview/exclude step
 * happens in a Modal so it doesn't have to stay on-screen while filling in
 * Job Details below; a compact summary chip replaces it once confirmed.
 */
export function ManualApply() {
  // ── Target Tables ──────────────────────────────────────────────────────
  const [domain, setDomain] = useState<string | undefined>();
  const [layer, setLayer] = useState<string | undefined>();
  const [databaseName, setDatabaseName] = useState('');
  const [pattern, setPattern] = useState('');

  const [previewOpen, setPreviewOpen] = useState(false);
  const [modalSelectedFqns, setModalSelectedFqns] = useState<string[]>([]);
  const [confirmedRows, setConfirmedRows] = useState<TableRow[]>([]);
  const [selectedFqns, setSelectedFqns] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);

  // ── Job Details ────────────────────────────────────────────────────────
  const [pipelineJob, setPipelineJob] = useState('');
  const [startTime, setStartTime] = useState('02:00');
  const [duration, setDuration] = useState(0);
  const [hkJob, setHkJob] = useState('');
  const [gate1Job, setGate1Job] = useState('');
  const [jobType, setJobType] = useState('controlm');
  const [jobFrequency, setJobFrequency] = useState<string | undefined>();
  const [ciNumber, setCiNumber] = useState('');
  const [showMore, setShowMore] = useState(false);

  const domains = useDomainsList(true);
  const jobs = useJobs();
  const matches = useTablesList({
    // size is capped at 250 by api/deps.py::PageParams (>250 -> 422).
    page: 1, size: 250, domain, layer, database_name: databaseName || undefined, search: pattern || undefined,
  });
  const bulk = useBulkControlM();

  const modalRows = previewOpen ? matches.data?.data ?? [] : [];
  const excludedCount = confirmedRows.length - selectedFqns.length;

  const jobOptions = useMemo(() => (jobs.data ?? []).map((j) => ({ value: j.job_name })), [jobs.data]);

  const handleSelectExistingJob = (value: string) => {
    const match = jobs.data?.find((j) => j.job_name === value);
    if (!match) return;
    // Only autofill from the job's OWN registry record when picking an
    // existing job -- not on every keystroke, so typing a new job name
    // doesn't flicker other fields based on a partial-match guess.
    if (match.expected_start_time) setStartTime(match.expected_start_time);
    setDuration(match.expected_duration_min ?? 0);
    if (match.job_frequency) setJobFrequency(match.job_frequency);
  };

  const handleOpenPreview = () => {
    setPreviewOpen(true);
    matches.refetch().then((r) => setModalSelectedFqns((r.data?.data ?? []).map((t) => t.table_fqn)));
  };

  const handleConfirmSelection = () => {
    setConfirmedRows(matches.data?.data ?? []);
    setSelectedFqns(modalSelectedFqns);
    setConfirmed(true);
    setPreviewOpen(false);
  };

  const handleApply = () => {
    const setFields: Record<string, unknown> = {
      controlm_pipeline_job: pipelineJob.trim(),
      controlm_hk_job: hkJob.trim(),
      dependent_on_controlm_job: gate1Job.trim() || pipelineJob.trim(),
      dependent_job_type: jobType,
      controlm_job_start_time: startTime,
      controlm_expected_duration_min: duration,
      // job_frequency isn't a stream_registry column -- the backend uses it
      // only as metadata for auto-registering the job into the Control-M
      // Job Registry, then discards it before building the table UPDATE.
      job_frequency: jobFrequency ?? '',
    };
    if (ciNumber.trim()) setFields.ci_number = ciNumber.trim();

    const excludeFqns = confirmedRows.filter((t) => !selectedFqns.includes(t.table_fqn)).map((t) => t.table_fqn);

    bulk.mutate(
      {
        filters: { domain, layer, database_name: databaseName || undefined, pattern: pattern || undefined, exclude_fqns: excludeFqns },
        set_fields: setFields,
        dry_run: false,
      },
      {
        onSuccess: (r) => {
          message.success(`✅ Applied to ${r.affected} table(s) (audit: ${r.audit_id}).`);
          // Full reset, including Target Tables -- a prior version kept the
          // confirmed selection alive after a successful apply (to avoid
          // flashing the "select tables first" hint right after success),
          // but that meant the "✅ All N table(s) selected" summary lingered
          // indefinitely -- surviving tab switches and even navigating away
          // and back (AntD Tabs keeps inactive panes mounted, so this
          // component's state was never actually reset by that navigation).
          // A completed apply should start the next one from a clean slate;
          // the "select tables first" hint below is now worded as neutral
          // guidance rather than a warning, so showing it again isn't jarring.
          setDomain(undefined);
          setLayer(undefined);
          setDatabaseName('');
          setPattern('');
          setConfirmed(false);
          setConfirmedRows([]);
          setSelectedFqns([]);
          setPipelineJob('');
          setStartTime('02:00');
          setDuration(0);
          setHkJob('');
          setGate1Job('');
          setJobType('controlm');
          setJobFrequency(undefined);
          setCiNumber('');
          setShowMore(false);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Bulk apply failed.'),
      },
    );
  };

  return (
    <div>
      <Card size="small" title="1. Target Tables" style={{ marginBottom: 16 }}>
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
        <Button onClick={handleOpenPreview}>🔍 Preview Matching Tables</Button>

        {confirmed && (
          <Alert
            style={{ marginTop: 12 }}
            type={excludedCount > 0 ? 'warning' : 'success'}
            showIcon
            message={
              excludedCount > 0
                ? `✅ ${selectedFqns.length} table(s) selected · ⛔ ${excludedCount} excluded`
                : `✅ All ${selectedFqns.length} table(s) selected`
            }
            action={<Button size="small" onClick={handleOpenPreview}>Change selection</Button>}
          />
        )}
      </Card>

      <Modal
        title={`${modalRows.length} table(s) matched`}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        width={800}
        footer={[
          <Button key="cancel" onClick={() => setPreviewOpen(false)}>Cancel</Button>,
          <Button key="confirm" type="primary" disabled={modalSelectedFqns.length === 0} onClick={handleConfirmSelection}>
            ✅ Confirm {modalSelectedFqns.length} Table(s)
          </Button>,
        ]}
      >
        <Table
          size="small"
          loading={matches.isFetching}
          dataSource={modalRows}
          rowKey="table_fqn"
          rowSelection={{ selectedRowKeys: modalSelectedFqns, onChange: (keys) => setModalSelectedFqns(keys as string[]) }}
          columns={[
            { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn' },
            { title: 'Domain', dataIndex: 'domain', key: 'domain' },
            { title: 'Layer', dataIndex: 'layer', key: 'layer' },
            { title: 'Tier', dataIndex: 'tier', key: 'tier' },
          ]}
          pagination={modalRows.length > 15 ? { pageSize: 15 } : false}
        />
      </Modal>

      <Card size="small" title="2. Job Details">
        <Row gutter={16} style={{ marginBottom: 12 }}>
          <Col span={12}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Control-M Job Name *</div>
            <AutoComplete
              style={{ width: '100%' }}
              options={jobOptions}
              value={pipelineJob}
              onChange={setPipelineJob}
              onSelect={handleSelectExistingJob}
              filterOption={(input, option) => String(option?.value ?? '').toLowerCase().includes(input.toLowerCase())}
              placeholder="Search an existing job, or type a new job name"
            />
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
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Gate 1 Job Type</div>
            <Select style={{ width: '100%' }} value={jobType} onChange={setJobType} options={JOB_TYPE_OPTIONS.map((j) => ({ value: j, label: j }))} />
          </Col>
        </Row>
        <Row gutter={16} style={{ marginBottom: 12 }}>
          <Col span={8}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Frequency (optional)</div>
            <Select
              allowClear style={{ width: '100%' }} value={jobFrequency} onChange={setJobFrequency}
              options={JOB_FREQUENCY_OPTIONS.map((f) => ({ value: f, label: f }))}
            />
          </Col>
        </Row>

        <div style={{ marginBottom: 24 }}>
          <Button type="link" style={{ padding: 0, marginBottom: showMore ? 12 : 0 }} onClick={() => setShowMore((v) => !v)}>
            {showMore ? '− Fewer options' : '+ More options'}
          </Button>
          {showMore && (
            <Row gutter={16}>
              <Col span={12}>
                <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>CI Number (optional — blank keeps existing)</div>
                <Input value={ciNumber} onChange={(e) => setCiNumber(e.target.value)} placeholder="CI-10300" />
              </Col>
            </Row>
          )}
        </div>

        <Button
          type="primary" disabled={!confirmed || selectedFqns.length === 0 || !pipelineJob.trim()}
          loading={bulk.isPending} onClick={handleApply}
        >
          🔗 Apply to {selectedFqns.length || 0} Table(s)
        </Button>
        {!confirmed && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#667085' }}>
            Select target tables above to enable Apply.
          </div>
        )}
      </Card>
    </div>
  );
}
