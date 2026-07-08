import { UploadSimple } from '@phosphor-icons/react';
import { Alert, AutoComplete, Button, Col, Input, InputNumber, message, Modal, Row, Select, Table, Tabs, Tag, Upload } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { useMemo, useState } from 'react';
import { useDeleteJob, useImportJobs, useJobs, useUpsertJob } from '../../../api/hooks/useControlm';
import { useDomainsList } from '../../../api/hooks/useDomains';
import type { JobRow } from '../../../api/types';
import { downloadCsv } from '../../../utils/csv';

const JOB_TYPE_OPTIONS = ['controlm', 'glue', 'lambda', 'step_functions', 'airflow', 'other'];
const JOB_FREQUENCY_OPTIONS = ['hourly', 'daily', 'weekly', 'monthly', 'every_trigger'];

/**
 * Job List -- the registry grid used to sit above the Add/Upload forms with
 * no search, no domain filter, and no way to remove a stale entry. The job
 * list is small (dozens, not thousands, of registered jobs), so search and
 * domain/type filters are all client-side over one already-fetched list --
 * the search box is an AutoComplete so it doubles as a suggestion dropdown
 * without a second round trip.
 */
function JobListTab() {
  const [search, setSearch] = useState('');
  const [domain, setDomain] = useState<string | undefined>();
  const [jobType, setJobType] = useState<string | undefined>();

  const jobs = useJobs();
  const domains = useDomainsList(true);
  const deleteJob = useDeleteJob();

  const rows = useMemo(
    () => (jobs.data ?? []).filter(
      (j) =>
        (!search || j.job_name.toLowerCase().includes(search.toLowerCase())) &&
        (!domain || j.domain === domain) &&
        (!jobType || j.job_type === jobType),
    ),
    [jobs.data, search, domain, jobType],
  );

  const suggestions = useMemo(() => {
    const names = Array.from(new Set((jobs.data ?? []).map((j) => j.job_name)));
    const needle = search.toLowerCase();
    return (needle ? names.filter((n) => n.toLowerCase().includes(needle)) : names)
      .slice(0, 20)
      .map((n) => ({ value: n }));
  }, [jobs.data, search]);

  const handleDelete = (jobName: string) => {
    Modal.confirm({
      title: `Remove "${jobName}" from the Control-M Job Registry?`,
      content: 'This only removes the registry entry -- it does not touch the real Control-M job or any table currently referencing it.',
      okText: 'Remove', okType: 'danger',
      onOk: () =>
        deleteJob.mutate(jobName, {
          onSuccess: (r) => message.success(`✅ Removed ${jobName} (audit: ${r.audit_id}).`),
          onError: (err) => message.error(err instanceof Error ? err.message : 'Failed.'),
        }),
    });
  };

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <AutoComplete
            style={{ width: '100%' }}
            options={suggestions}
            value={search}
            onChange={setSearch}
            placeholder="Search by job name"
            allowClear
            filterOption={false}
          />
        </Col>
        <Col span={8}>
          <Select
            style={{ width: '100%' }} allowClear placeholder="Filter by Domain"
            value={domain} onChange={setDomain}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          />
        </Col>
        <Col span={8}>
          <Select
            style={{ width: '100%' }} allowClear placeholder="Filter by Job Type"
            value={jobType} onChange={setJobType}
            options={JOB_TYPE_OPTIONS.map((j) => ({ value: j, label: j }))}
          />
        </Col>
      </Row>

      <Table<JobRow>
        size="small"
        loading={jobs.isLoading}
        dataSource={rows}
        rowKey="job_name"
        pagination={rows.length > 15 ? { pageSize: 15 } : false}
        locale={{ emptyText: jobs.isLoading ? 'Loading…' : 'No Control-M jobs registered yet.' }}
        title={() => (
          <Row justify="space-between" align="middle">
            <span>{rows.length} job(s)</span>
            <Button
              size="small"
              onClick={() => downloadCsv(rows, 'zamboni_controlm_jobs.csv')}
              disabled={rows.length === 0}
            >
              ⬇️ Export CSV
            </Button>
          </Row>
        )}
        columns={[
          { title: 'Job Name', dataIndex: 'job_name', key: 'job_name' },
          { title: 'Type', dataIndex: 'job_type', key: 'job_type' },
          { title: 'Domain', dataIndex: 'domain', key: 'domain' },
          { title: 'Frequency', dataIndex: 'job_frequency', key: 'job_frequency', render: (v: string) => v || '—' },
          { title: 'Description', dataIndex: 'description', key: 'description' },
          { title: 'Start', dataIndex: 'expected_start_time', key: 'expected_start_time' },
          { title: 'Duration (min)', dataIndex: 'expected_duration_min', key: 'expected_duration_min' },
          {
            title: 'Active', dataIndex: 'active', key: 'active',
            render: (v: boolean) => (v ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>),
          },
          {
            title: '', key: 'actions', width: 80,
            render: (_: unknown, row: JobRow) => (
              <Button size="small" danger type="text" onClick={() => handleDelete(row.job_name)}>
                Remove
              </Button>
            ),
          },
        ]}
      />
    </div>
  );
}

function AddSingleJob() {
  const [jobName, setJobName] = useState('');
  const [jobType, setJobType] = useState('controlm');
  const [domain, setDomain] = useState('');
  const [startTime, setStartTime] = useState('');
  const [duration, setDuration] = useState(0);
  const [jobFrequency, setJobFrequency] = useState<string | undefined>();
  const [description, setDescription] = useState('');

  const domains = useDomainsList(true);
  const upsert = useUpsertJob();

  const handleAdd = () => {
    if (!jobName.trim()) {
      message.error('Job Name is required.');
      return;
    }
    upsert.mutate(
      {
        job_name: jobName.trim(), job_type: jobType, domain, description,
        expected_start_time: startTime, expected_duration_min: duration,
        job_frequency: jobFrequency ?? '',
      },
      {
        onSuccess: (r) => {
          message.success(`✅ ${jobName.trim()} added to registry (audit: ${r.audit_id}).`);
          setJobName(''); setDomain(''); setStartTime(''); setDuration(0); setDescription(''); setJobFrequency(undefined);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Failed.'),
      },
    );
  };

  return (
    <Row gutter={16}>
      <Col span={8}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Name *</div>
        <Input value={jobName} onChange={(e) => setJobName(e.target.value)} placeholder="ACE-DA-FIN-APS-INGEST-PRD" style={{ marginBottom: 12 }} />
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Type</div>
        <Select style={{ width: '100%' }} value={jobType} onChange={setJobType} options={JOB_TYPE_OPTIONS.map((j) => ({ value: j, label: j }))} />
      </Col>
      <Col span={8}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain (optional)</div>
        <Select
          allowClear value={domain || undefined} onChange={(v) => setDomain(v ?? '')}
          options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          style={{ marginBottom: 12, width: '100%' }}
        />
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Frequency (optional)</div>
        <Select
          allowClear value={jobFrequency} onChange={setJobFrequency}
          options={JOB_FREQUENCY_OPTIONS.map((f) => ({ value: f, label: f }))}
          style={{ width: '100%' }}
        />
      </Col>
      <Col span={8}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected start time (HH:MM)</div>
        <Input value={startTime} onChange={(e) => setStartTime(e.target.value)} placeholder="02:00" style={{ marginBottom: 12 }} />
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected duration (min)</div>
        <InputNumber min={0} max={480} style={{ width: '100%' }} value={duration} onChange={(v) => setDuration(v ?? 0)} />
      </Col>
      <Col span={24} style={{ marginTop: 12 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Description</div>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Finance APS ingest pipeline" style={{ marginBottom: 12 }} />
        <Button type="primary" loading={upsert.isPending} onClick={handleAdd}>➕ Add Job</Button>
      </Col>
    </Row>
  );
}

function BulkUploadJobs() {
  const importJobs = useImportJobs();

  const customRequest = (options: UploadRequestOption) => {
    importJobs.mutate(options.file as File, {
      onSuccess: (r) => {
        message.success(`✅ Imported ${r.imported} job(s)${r.failed ? `, ${r.failed} failed` : ''} (audit: ${r.audit_id}).`);
        options.onSuccess?.({});
      },
      onError: (err) => {
        message.error(err instanceof Error ? err.message : 'Import failed.');
        options.onError?.(err as Error);
      },
    });
  };

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Upload a CSV with your full Control-M job list. Existing jobs are updated (upsert)."
        description="Required column: job_name. Optional: job_type, domain, description, expected_start_time, expected_duration_min, job_frequency."
      />
      <Upload accept=".csv" showUploadList={false} customRequest={customRequest}>
        <Button icon={<UploadSimple size={14} />} loading={importJobs.isPending}>Upload Control-M Jobs CSV</Button>
      </Upload>
    </div>
  );
}

/** Control-M Job Registry -- first tab of the Control-M Integration page. */
export function JobRegistry() {
  return (
    <Tabs
      items={[
        { key: 'list', label: '📋 Job List', children: <JobListTab /> },
        { key: 'add', label: '➕ Add Single Job', children: <AddSingleJob /> },
        { key: 'upload', label: '📥 Bulk Upload CSV', children: <BulkUploadJobs /> },
      ]}
    />
  );
}
