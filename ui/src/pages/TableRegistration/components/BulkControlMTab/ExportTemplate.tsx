import { DownloadSimple } from '@phosphor-icons/react';
import { Alert, Button, Card, Collapse, Table } from 'antd';
import { useEffect } from 'react';
import { useExportJobMapping } from '../../../../api/hooks/useTables';
import { downloadRawCsv } from '../../../../utils/csv';

const COLUMN_GUIDE = [
  { column: 'domain', required: '✅', example: 'finance', notes: 'Must match a Zamboni domain' },
  { column: 'layer', required: 'optional', example: 'staging', notes: 'Leave blank = all layers' },
  { column: 'database_name', required: 'optional', example: 'finance_staging_db', notes: 'Leave blank = all databases' },
  { column: 'table_pattern', required: 'optional', example: 'aps_%', notes: 'SQL LIKE wildcard' },
  { column: 'controlm_job_name', required: '✅', example: 'ACE-DA-FIN-APS-INGEST-PRD', notes: 'Control-M job that loads data' },
  { column: 'job_type', required: 'optional', example: 'controlm', notes: 'Defaults to controlm' },
  { column: 'hk_controlm_job', required: 'optional', example: 'ACE-DA-FIN-HK-PRD', notes: 'Control-M job that runs Zamboni HK' },
  { column: 'aws_gate1_job', required: 'optional', example: '—', notes: 'AWS job for Gate 1. Blank = use controlm_job_name' },
  { column: 'job_start_time', required: 'optional', example: '02:00', notes: '24h HH:MM' },
  { column: 'expected_duration_min', required: 'optional', example: '45', notes: 'Typical job run time' },
];

/**
 * Export Mapping Template (2_Table_Registration.py bc_tab_export). The
 * server returns a JSON-wrapped CSV string (api/services/tables_svc.py::
 * export_job_mapping, locked in Phase 2), so the preview table below parses
 * it client-side for display only -- the download button ships the raw
 * string untouched, which is the actual source of truth for domain teams.
 */
export function ExportTemplate() {
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

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Export a pre-filled CSV showing all unique domain/layer/database combinations."
        description="Share with domain teams — they fill in the job names and return the file for import."
      />

      {exportQuery.isFetching && <div>Loading…</div>}

      {previewRows.length > 0 && (
        <Card size="small" title={`${previewRows.length} domain/layer/database row(s)`} style={{ marginBottom: 16 }}>
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
        items={[
          {
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
          },
        ]}
      />
    </div>
  );
}
