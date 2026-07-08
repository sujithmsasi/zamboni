import { UploadSimple } from '@phosphor-icons/react';
import { Alert, AutoComplete, Button, Card, Col, Input, InputNumber, message, Modal, Row, Select, Table, Tabs, Tag, Upload } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { useEffect, useMemo, useState } from 'react';
import type { JobImportRow } from '../../../api/hooks/useControlm';
import { useDeleteJob, useImportJobs, useJobMappedTables, useJobs, useUpsertJob } from '../../../api/hooks/useControlm';
import { useDomainsList } from '../../../api/hooks/useDomains';
import type { JobRow } from '../../../api/types';
import { downloadCsv, downloadRawCsv } from '../../../utils/csv';

const JOB_TYPE_OPTIONS = ['controlm', 'glue', 'lambda', 'step_functions', 'airflow', 'other'];
const JOB_FREQUENCY_OPTIONS = ['hourly', 'daily', 'weekly', 'monthly', 'every_trigger'];

/** Drill-in popup for Job List's "Tables Mapped" count -- which tables
 * reference this job, and via which of the three Control-M role columns. */
function MappedTablesModal({ jobName, onClose }: { jobName: string | null; onClose: () => void }) {
  const mapped = useJobMappedTables(jobName);

  return (
    <Modal title={`Tables mapped to "${jobName}"`} open={!!jobName} onCancel={onClose} footer={null} width={800}>
      <Table
        size="small"
        loading={mapped.isLoading}
        dataSource={mapped.data ?? []}
        rowKey="table_fqn"
        pagination={(mapped.data?.length ?? 0) > 10 ? { pageSize: 10 } : false}
        locale={{ emptyText: 'No tables currently reference this job.' }}
        columns={[
          { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn' },
          { title: 'Domain', dataIndex: 'domain', key: 'domain' },
          { title: 'Layer', dataIndex: 'layer', key: 'layer' },
          {
            title: 'Role', key: 'role',
            render: (_: unknown, row: Record<string, unknown>) => {
              const roles: string[] = [];
              if (row.controlm_pipeline_job === jobName) roles.push('Pipeline');
              if (row.controlm_hk_job === jobName) roles.push('HK');
              if (row.dependent_on_controlm_job === jobName) roles.push('Gate 1');
              return roles.map((r) => <Tag key={r}>{r}</Tag>);
            },
          },
        ]}
      />
    </Modal>
  );
}

/** Edit an existing job's registry metadata. Job Name itself isn't
 * editable -- upsert_job is INSERT OR REPLACE keyed on job_name, so
 * "renaming" here would silently create a second entry and orphan the
 * original rather than rename it. */
function EditJobModal({ job, onClose }: { job: JobRow | null; onClose: () => void }) {
  const [jobType, setJobType] = useState('controlm');
  const [domain, setDomain] = useState('');
  const [startTime, setStartTime] = useState('');
  const [duration, setDuration] = useState(0);
  const [jobFrequency, setJobFrequency] = useState<string | undefined>();
  const [description, setDescription] = useState('');

  const domains = useDomainsList(true);
  const upsert = useUpsertJob();

  useEffect(() => {
    if (!job) return;
    setJobType(job.job_type || 'controlm');
    setDomain(job.domain || '');
    setStartTime(job.expected_start_time || '');
    setDuration(job.expected_duration_min ?? 0);
    setJobFrequency(job.job_frequency || undefined);
    setDescription(job.description || '');
  }, [job]);

  const handleSave = () => {
    if (!job) return;
    upsert.mutate(
      {
        job_name: job.job_name, job_type: jobType, domain, description,
        expected_start_time: startTime, expected_duration_min: duration,
        job_frequency: jobFrequency ?? '',
      },
      {
        onSuccess: (r) => {
          message.success(`✅ ${job.job_name} updated (audit: ${r.audit_id}).`);
          onClose();
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Failed.'),
      },
    );
  };

  return (
    <Modal
      title={`Edit "${job?.job_name}"`}
      open={!!job}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={upsert.isPending}
      okText="Save Changes"
    >
      <Row gutter={16}>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Type</div>
          <Select style={{ width: '100%', marginBottom: 12 }} value={jobType} onChange={setJobType} options={JOB_TYPE_OPTIONS.map((j) => ({ value: j, label: j }))} />
        </Col>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain</div>
          <Select
            allowClear value={domain || undefined} onChange={(v) => setDomain(v ?? '')}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
            style={{ width: '100%', marginBottom: 12 }}
          />
        </Col>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected start time (HH:MM)</div>
          <Input value={startTime} onChange={(e) => setStartTime(e.target.value)} placeholder="02:00" style={{ marginBottom: 12 }} />
        </Col>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected duration (min)</div>
          <InputNumber min={0} max={480} style={{ width: '100%', marginBottom: 12 }} value={duration} onChange={(v) => setDuration(v ?? 0)} />
        </Col>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Job Frequency</div>
          <Select
            allowClear value={jobFrequency} onChange={setJobFrequency}
            options={JOB_FREQUENCY_OPTIONS.map((f) => ({ value: f, label: f }))}
            style={{ width: '100%', marginBottom: 12 }}
          />
        </Col>
        <Col span={24}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Description</div>
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Finance APS ingest pipeline" />
        </Col>
      </Row>
    </Modal>
  );
}

/**
 * Job List -- the registry grid used to sit above the Add/Upload forms with
 * no search, no domain filter, and no way to remove or edit a stale entry.
 * The job list is small (dozens, not thousands, of registered jobs), so
 * search and domain/type filters are all client-side over one already-
 * fetched list -- the search box is an AutoComplete so it doubles as a
 * suggestion dropdown without a second round trip.
 */
function JobListTab() {
  const [search, setSearch] = useState('');
  const [domain, setDomain] = useState<string | undefined>();
  const [jobType, setJobType] = useState<string | undefined>();
  const [editingJob, setEditingJob] = useState<JobRow | null>(null);
  const [mappedTablesFor, setMappedTablesFor] = useState<string | null>(null);

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

  const handleDelete = (jobName: string, tablesMapped: number) => {
    Modal.confirm({
      title: `Remove "${jobName}" from the Control-M Job Registry?`,
      content: tablesMapped > 0
        ? `This job is currently referenced by ${tablesMapped} table(s). Removing it only deletes the registry entry -- those tables will keep showing "${jobName}" as their Control-M job until edited individually.`
        : 'This only removes the registry entry -- it does not touch the real Control-M job or any table currently referencing it.',
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
            title: 'Tables Mapped', dataIndex: 'tables_mapped', key: 'tables_mapped',
            sorter: (a: JobRow, b: JobRow) => a.tables_mapped - b.tables_mapped,
            render: (v: number, row: JobRow) =>
              v > 0 ? (
                <Button size="small" type="link" style={{ padding: 0 }} onClick={() => setMappedTablesFor(row.job_name)}>
                  {v}
                </Button>
              ) : (
                <span style={{ color: '#98A2B3' }}>0</span>
              ),
          },
          {
            title: 'Active', dataIndex: 'active', key: 'active',
            render: (v: boolean) => (v ? <Tag color="green">Yes</Tag> : <Tag>No</Tag>),
          },
          {
            title: '', key: 'actions', width: 140,
            render: (_: unknown, row: JobRow) => (
              <>
                <Button size="small" type="text" onClick={() => setEditingJob(row)}>
                  Edit
                </Button>
                <Button size="small" danger type="text" onClick={() => handleDelete(row.job_name, row.tables_mapped)}>
                  Remove
                </Button>
              </>
            ),
          },
        ]}
      />

      <EditJobModal job={editingJob} onClose={() => setEditingJob(null)} />
      <MappedTablesModal jobName={mappedTablesFor} onClose={() => setMappedTablesFor(null)} />
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

const SAMPLE_CSV = [
  'job_name,job_type,domain,description,expected_start_time,expected_duration_min,job_frequency',
  'ACE-DA-FIN-APS-INGEST-PRD,controlm,finance,Finance APS ingest pipeline,02:00,45,daily',
  'ACE-DA-FIN-APS-HK-PRD,controlm,finance,Zamboni HK trigger for APS,03:00,15,daily',
].join('\n');

/**
 * Bulk Upload CSV used to upsert immediately on upload with no review step
 * -- unlike every other CSV import in this app (Import Job Mapping already
 * has a dry-run preview). Now: upload -> dry-run preview in a table with
 * row selection -> Save only the selected rows. Deliberately no inline
 * cell editing -- if a row is wrong, exclude it and fix+re-upload the CSV,
 * same as Manual Bulk Apply's preview modal only supports select/exclude.
 */
function BulkUploadJobs() {
  const [file, setFile] = useState<File | null>(null);
  const [previewRows, setPreviewRows] = useState<JobImportRow[] | null>(null);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const importJobs = useImportJobs();

  const customRequest = (options: UploadRequestOption) => {
    const f = options.file as File;
    setFile(f);
    importJobs.mutate(
      { file: f, dryRun: true },
      {
        onSuccess: (r) => {
          setPreviewRows(r.rows);
          setSelectedNames(r.rows.map((row) => row.job_name));
          options.onSuccess?.({});
        },
        onError: (err) => {
          message.error(err instanceof Error ? err.message : 'CSV parsing failed.');
          options.onError?.(err as Error);
        },
      },
    );
  };

  const handleConfirm = () => {
    if (!file || !previewRows) return;
    const excludeJobNames = previewRows.filter((r) => !selectedNames.includes(r.job_name)).map((r) => r.job_name);
    importJobs.mutate(
      { file, dryRun: false, excludeJobNames },
      {
        onSuccess: (r) => {
          message.success(`✅ Imported ${r.imported} job(s)${r.failed ? `, ${r.failed} failed` : ''} (audit: ${r.audit_id}).`);
          setPreviewRows(null);
          setFile(null);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Import failed.'),
      },
    );
  };

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Upload a CSV with your full Control-M job list. Existing jobs are updated (upsert)."
        description="Required column: job_name. Optional: job_type, domain, description, expected_start_time, expected_duration_min, job_frequency."
      />
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col>
          <Upload accept=".csv" showUploadList={false} customRequest={customRequest}>
            <Button icon={<UploadSimple size={14} />} loading={importJobs.isPending && !previewRows}>Upload Control-M Jobs CSV</Button>
          </Upload>
        </Col>
        <Col>
          <Button onClick={() => downloadRawCsv(SAMPLE_CSV, 'zamboni_controlm_jobs_sample.csv')}>
            ⬇️ Download Sample CSV
          </Button>
        </Col>
      </Row>

      {previewRows && (
        <Card size="small" title={`${previewRows.length} job(s) parsed — review before saving`}>
          <Table<JobImportRow>
            size="small"
            dataSource={previewRows}
            rowKey="job_name"
            rowSelection={{ selectedRowKeys: selectedNames, onChange: (keys) => setSelectedNames(keys as string[]) }}
            pagination={previewRows.length > 10 ? { pageSize: 10 } : false}
            columns={[
              { title: 'Job Name', dataIndex: 'job_name', key: 'job_name' },
              { title: 'Type', dataIndex: 'job_type', key: 'job_type' },
              { title: 'Domain', dataIndex: 'domain', key: 'domain' },
              { title: 'Frequency', dataIndex: 'job_frequency', key: 'job_frequency', render: (v: string) => v || '—' },
              { title: 'Start', dataIndex: 'expected_start_time', key: 'expected_start_time' },
              { title: 'Duration (min)', dataIndex: 'expected_duration_min', key: 'expected_duration_min' },
            ]}
          />
          <div style={{ margin: '12px 0' }}>
            {selectedNames.length < previewRows.length ? (
              <Tag color="gold">✅ {selectedNames.length} will be saved · ⛔ {previewRows.length - selectedNames.length} excluded</Tag>
            ) : (
              <Tag color="green">✅ All {selectedNames.length} job(s) will be saved</Tag>
            )}
          </div>
          <Button type="primary" disabled={selectedNames.length === 0} loading={importJobs.isPending} onClick={handleConfirm}>
            💾 Save {selectedNames.length} Job(s)
          </Button>
        </Card>
      )}
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
