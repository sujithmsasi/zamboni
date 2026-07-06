import { UploadSimple } from '@phosphor-icons/react';
import { Alert, Button, Card, Col, Input, InputNumber, message, Row, Select, Table, Tabs, Upload } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { useState } from 'react';
import { useImportJobs, useJobs, useUpsertJob } from '../../../../api/hooks/useControlm';
import { useDomainsList } from '../../../../api/hooks/useDomains';

const JOB_TYPE_OPTIONS = ['controlm', 'glue', 'lambda', 'step_functions', 'airflow', 'other'];

function AddSingleJob() {
  const [jobName, setJobName] = useState('');
  const [jobType, setJobType] = useState('controlm');
  const [domain, setDomain] = useState('');
  const [startTime, setStartTime] = useState('');
  const [duration, setDuration] = useState(0);
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
      },
      {
        onSuccess: (r) => {
          message.success(`✅ ${jobName.trim()} added to registry (audit: ${r.audit_id}).`);
          setJobName(''); setDomain(''); setStartTime(''); setDuration(0); setDescription('');
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
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected start time (HH:MM)</div>
        <Input value={startTime} onChange={(e) => setStartTime(e.target.value)} placeholder="02:00" />
      </Col>
      <Col span={8}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Expected duration (min)</div>
        <InputNumber min={0} max={480} style={{ width: '100%', marginBottom: 12 }} value={duration} onChange={(v) => setDuration(v ?? 0)} />
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Description</div>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Finance APS ingest pipeline" />
      </Col>
      <Col span={24} style={{ marginTop: 16 }}>
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
        description="Required column: job_name. Optional: job_type, domain, description, expected_start_time, expected_duration_min."
      />
      <Upload accept=".csv" showUploadList={false} customRequest={customRequest}>
        <Button icon={<UploadSimple size={14} />} loading={importJobs.isPending}>Upload Control-M Jobs CSV</Button>
      </Upload>
    </div>
  );
}

/** Control-M Job Registry (2_Table_Registration.py bc_tab_jobs). */
export function JobRegistry() {
  const jobs = useJobs();

  return (
    <div>
      {(jobs.data ?? []).length > 0 && (
        <Card size="small" title={`${jobs.data?.length} registered job(s)`} style={{ marginBottom: 16 }}>
          <Table
            size="small"
            loading={jobs.isLoading}
            dataSource={jobs.data ?? []}
            rowKey="job_name"
            pagination={(jobs.data?.length ?? 0) > 15 ? { pageSize: 15 } : false}
            columns={[
              { title: 'Job Name', dataIndex: 'job_name', key: 'job_name' },
              { title: 'Type', dataIndex: 'job_type', key: 'job_type' },
              { title: 'Domain', dataIndex: 'domain', key: 'domain' },
              { title: 'Description', dataIndex: 'description', key: 'description' },
              { title: 'Start', dataIndex: 'expected_start_time', key: 'expected_start_time' },
              { title: 'Duration (min)', dataIndex: 'expected_duration_min', key: 'expected_duration_min' },
            ]}
          />
        </Card>
      )}

      <Tabs
        items={[
          { key: 'add', label: '➕ Add Single Job', children: <AddSingleJob /> },
          { key: 'upload', label: '📥 Bulk Upload CSV', children: <BulkUploadJobs /> },
        ]}
      />
    </div>
  );
}
