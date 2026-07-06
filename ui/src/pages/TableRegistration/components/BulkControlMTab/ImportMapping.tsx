import { UploadSimple } from '@phosphor-icons/react';
import { Alert, Button, Card, Table, Upload, message } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { useState } from 'react';
import { useImportJobMapping } from '../../../../api/hooks/useTables';
import type { JobMappingRow } from '../../../../api/types';

const COLUMNS = [
  { title: 'Job', dataIndex: 'job', key: 'job' },
  { title: 'Type', dataIndex: 'job_type', key: 'job_type' },
  { title: 'Domain', dataIndex: 'domain', key: 'domain' },
  { title: 'Layer', dataIndex: 'layer', key: 'layer' },
  { title: 'DB', dataIndex: 'database_name', key: 'database_name' },
  { title: 'Pattern', dataIndex: 'table_pattern', key: 'table_pattern' },
  { title: 'Tables Matched', dataIndex: 'tables_matched', key: 'tables_matched' },
];

/**
 * Import Job Mapping (2_Table_Registration.py bc_tab_import). The backend's
 * dry_run param already produces the same per-row match report either way
 * (api/services/tables_svc.py::import_job_mapping) -- upload once with
 * dry_run=true for the preview, then resubmit the same file with
 * dry_run=false to actually apply, rather than a separate client-side CSV
 * parser duplicating the server's column-rename/default-fill logic.
 */
export function ImportMapping() {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<JobMappingRow[] | null>(null);
  const importMapping = useImportJobMapping();

  const runImport = (f: File, dryRun: boolean) => {
    importMapping.mutate(
      { file: f, dryRun },
      {
        onSuccess: (r) => {
          setReport(r.rows);
          if (!dryRun) message.success(`✅ Applied mapping (audit: ${r.audit_id}).`);
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
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Upload a Job Mapping CSV from domain teams. Each row maps one job to a set of tables by domain/layer/pattern."
        description="Use the Export Mapping Template tab to generate a pre-filled CSV. Required columns: domain, controlm_job_name."
      />
      <Upload accept=".csv" showUploadList={false} customRequest={customRequest}>
        <Button icon={<UploadSimple size={14} />} loading={importMapping.isPending && !!file}>Upload Job Mapping CSV</Button>
      </Upload>

      {report && (
        <Card size="small" style={{ marginTop: 16 }} title={`${report.length} job mapping row(s) — match preview`}>
          <Table size="small" dataSource={report} rowKey="job" columns={COLUMNS} pagination={report.length > 15 ? { pageSize: 15 } : false} />
          <Alert style={{ margin: '12px 0' }} type="info" message={`${totalMatched} table(s) will be updated across all rows.`} />
          <Button
            type="primary" disabled={totalMatched === 0 || !file} loading={importMapping.isPending}
            onClick={() => file && runImport(file, false)}
          >
            📥 Apply Mapping to {totalMatched} Table(s)
          </Button>
        </Card>
      )}
    </div>
  );
}
