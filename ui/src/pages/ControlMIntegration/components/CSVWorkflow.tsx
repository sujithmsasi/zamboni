import { DownloadSimple, UploadSimple } from '@phosphor-icons/react';
import { Alert, Button, Card, Collapse, Table, Upload, message } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { useEffect, useState } from 'react';
import { useExportJobMapping, useImportJobMapping } from '../../../api/hooks/useTables';
import type { JobMappingRow } from '../../../api/types';
import { downloadRawCsv } from '../../../utils/csv';

const COLUMN_GUIDE = [
  { column: 'domain', required: '✅', example: 'finance', notes: 'Must match a Zamboni domain' },
  { column: 'layer', required: 'optional', example: 'staging', notes: 'Leave blank = all layers' },
  { column: 'database_name', required: 'optional', example: 'finance_staging_db', notes: 'Leave blank = all databases' },
  { column: 'table_pattern', required: 'optional', example: 'aps_%', notes: 'SQL LIKE wildcard' },
  { column: 'controlm_job_name', required: '✅', example: 'ACE-DA-FIN-APS-INGEST-PRD', notes: 'Control-M job that loads data' },
  { column: 'hk_controlm_job', required: 'optional', example: 'ACE-DA-FIN-HK-PRD', notes: 'Control-M job that runs Zamboni HK' },
  { column: 'aws_gate1_job', required: 'optional', example: '—', notes: 'AWS job for Gate 1. Blank = use controlm_job_name' },
  { column: 'job_type', required: 'optional', example: 'controlm', notes: "AWS service type for aws_gate1_job's Gate 1 completion check -- not controlm_job_name's type. Defaults to controlm" },
  { column: 'job_start_time', required: 'optional', example: '02:00', notes: '24h HH:MM' },
  { column: 'expected_duration_min', required: 'optional', example: '45', notes: 'Typical job run time' },
];

const IMPORT_COLUMNS = [
  { title: 'Job', dataIndex: 'job', key: 'job' },
  { title: 'Domain', dataIndex: 'domain', key: 'domain' },
  { title: 'Layer', dataIndex: 'layer', key: 'layer' },
  { title: 'DB', dataIndex: 'database_name', key: 'database_name' },
  { title: 'Pattern', dataIndex: 'table_pattern', key: 'table_pattern' },
  { title: 'Gate 1 Job Type', dataIndex: 'job_type', key: 'job_type' },
  { title: 'Tables Matched', dataIndex: 'tables_matched', key: 'tables_matched' },
];

/**
 * CSV Workflow -- merges the old Export Mapping Template + Import Job
 * Mapping tabs into one guided two-step flow (they were two alternatives
 * for the same goal, not two unrelated features, so peer tabs with no
 * signal they're sequential was the wrong shape). Also fixes: the upload
 * preview/report used to stay on screen indefinitely after a real (non
 * dry-run) apply, including across a navigate-away-and-back -- now it
 * clears the moment the apply actually succeeds, since a completed action
 * has nothing left to preview.
 */
export function CSVWorkflow() {
  const exportQuery = useExportJobMapping();
  useEffect(() => {
    exportQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const csvText = exportQuery.data?.csv ?? '';
  const lines = csvText.trim().split('\n');
  const header = lines[0]?.split(',') ?? [];
  const previewRows = lines.slice(1).map((line, i) => {
    const cells = line.split(',');
    const row: Record<string, string> = { key: String(i) };
    header.forEach((h, idx) => { row[h] = cells[idx] ?? ''; });
    return row;
  });

  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<JobMappingRow[] | null>(null);
  const importMapping = useImportJobMapping();

  const runImport = (f: File, dryRun: boolean) => {
    importMapping.mutate(
      { file: f, dryRun },
      {
        onSuccess: (r) => {
          if (!dryRun) {
            message.success(`✅ Applied mapping (audit: ${r.audit_id}).`);
            // Real apply succeeded -- nothing left to preview, so clear the
            // upload state rather than leaving a stale "N table(s) will be
            // updated" report on screen (previously persisted indefinitely,
            // including across navigating away and back to this tab).
            setReport(null);
            setFile(null);
            return;
          }
          setReport(r.rows);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Import failed.'),
      },
    );
  };

  const customRequest = (options: UploadRequestOption) => {
    const f = options.file as File;
    setFile(f);
    runImport(f, true);
    options.onSuccess?.({});
  };

  const totalMatched = (report ?? []).reduce((sum, r) => sum + Math.max(r.tables_matched, 0), 0);

  return (
    <div>
      <Collapse
        defaultActiveKey={['step1']}
        style={{ marginBottom: 16 }}
        items={[{
          key: 'step1',
          label: 'Step 1 — Download Template',
          children: (
            <>
              <Alert
                style={{ marginBottom: 16 }} type="info" showIcon
                message="Export a pre-filled CSV showing all unique domain/layer/database combinations."
                description="Share with domain teams — they fill in the job names and return the file for Step 2 below."
              />

              {exportQuery.isFetching && <div>Loading…</div>}

              {previewRows.length > 0 && (
                <Card size="small" type="inner" title={`${previewRows.length} domain/layer/database row(s)`} style={{ marginBottom: 16 }}>
                  <Table
                    size="small"
                    dataSource={previewRows}
                    columns={header.map((h) => ({ title: h, dataIndex: h, key: h }))}
                    pagination={previewRows.length > 15 ? { pageSize: 15 } : false}
                    scroll={{ x: 'max-content' }}
                  />
                </Card>
              )}

              <Button
                icon={<DownloadSimple size={14} />}
                disabled={!csvText}
                onClick={() => downloadRawCsv(csvText, 'zamboni_job_mapping_template.csv')}
                style={{ marginBottom: 16 }}
              >
                Download Job Mapping Template CSV
              </Button>

              <Collapse
                items={[{
                  key: 'guide',
                  label: 'CSV column guide for domain teams',
                  children: (
                    <Table
                      size="small"
                      dataSource={COLUMN_GUIDE}
                      rowKey="column"
                      pagination={false}
                      columns={[
                        { title: 'Column', dataIndex: 'column', key: 'column' },
                        { title: 'Required', dataIndex: 'required', key: 'required' },
                        { title: 'Example', dataIndex: 'example', key: 'example' },
                        { title: 'Notes', dataIndex: 'notes', key: 'notes' },
                      ]}
                    />
                  ),
                }]}
              />
            </>
          ),
        }]}
      />

      <Card size="small" title="Step 2 — Upload Completed Mapping">
        <Alert
          style={{ marginBottom: 16 }} type="info" showIcon
          message="Upload the Job Mapping CSV once domain teams have filled in job names."
          description="Each row maps one job to a set of tables by domain/layer/pattern. Required columns: domain, controlm_job_name."
        />
        <Upload accept=".csv" showUploadList={false} customRequest={customRequest}>
          <Button icon={<UploadSimple size={14} />} loading={importMapping.isPending && !!file}>Upload Job Mapping CSV</Button>
        </Upload>

        {report && (
          <Card size="small" type="inner" style={{ marginTop: 16 }} title={`${report.length} job mapping row(s) — match preview`}>
            <Table size="small" dataSource={report} rowKey="job" columns={IMPORT_COLUMNS} pagination={report.length > 15 ? { pageSize: 15 } : false} />
            <Alert style={{ margin: '12px 0' }} type="info" message={`${totalMatched} table(s) will be updated across all rows.`} />
            <Button
              type="primary" disabled={totalMatched === 0 || !file} loading={importMapping.isPending}
              onClick={() => file && runImport(file, false)}
            >
              📥 Apply Mapping to {totalMatched} Table(s)
            </Button>
          </Card>
        )}
      </Card>
    </div>
  );
}
