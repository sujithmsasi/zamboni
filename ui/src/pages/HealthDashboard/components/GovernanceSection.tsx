import { Button, Col, message, Row, Select, Statistic } from 'antd';
import { useState } from 'react';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useExecutionsList } from '../../../api/hooks/useExecutions';
import { useConflicts, useConflictsExportAll, useRescanConflicts } from '../../../api/hooks/useGates';
import type { ConflictRow, ConflictSummary } from '../../../api/types';
import { DataGrid } from '../../../components/DataGrid';
import { StateBadge } from '../../../components/StateBadge';
import { downloadCsv } from '../../../utils/csv';

const CONFLICT_COLUMNS = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 110 },
  { title: 'Layer', dataIndex: 'layer', key: 'layer', width: 90 },
  { title: 'Tier', dataIndex: 'tier', key: 'tier', width: 90 },
  {
    title: 'Compaction', dataIndex: 'aws_opt_compaction', key: 'aws_opt_compaction', width: 100,
    render: (v: boolean) => (v ? '✅' : ''),
  },
  {
    title: 'Retention', dataIndex: 'aws_opt_retention', key: 'aws_opt_retention', width: 100,
    render: (v: boolean) => (v ? '✅' : ''),
  },
  {
    title: 'Orphan', dataIndex: 'aws_opt_orphan', key: 'aws_opt_orphan', width: 90,
    render: (v: boolean) => (v ? '✅' : ''),
  },
  { title: 'Checked At', dataIndex: 'aws_opt_checked_at', key: 'aws_opt_checked_at', width: 160 },
  { title: 'Override Until', dataIndex: 'gate0_override_until', key: 'gate0_override_until', width: 160 },
];

const FAILURE_COLUMNS = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 110 },
  { title: 'Operation', dataIndex: 'operation', key: 'operation', width: 110 },
  {
    title: 'Integrity', dataIndex: 'integrity_status', key: 'integrity_status', width: 100,
    render: (s: string) => <StateBadge state={s} />,
  },
  { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 170 },
  { title: 'Error', dataIndex: 'error_message', key: 'error_message', ellipsis: true },
];

interface GovernanceSectionProps {
  conflicts: ConflictSummary;
}

/**
 * The Dual-Optimizer Risk Report -- replaces the 4_Health_Dashboard.py
 * Streamlit "Maintenance Governance" stopgap section (Phase 1c) as the
 * governance showcase surface. CSV export uses the client-side downloadCsv
 * helper (utils/csv.ts) since GET /api/conflicts?export=csv returns the full
 * JSON row set, not a real CSV stream -- see that file's doc comment.
 */
export function GovernanceSection({ conflicts }: GovernanceSectionProps) {
  const [domain, setDomain] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const conflictsQuery = useConflicts(page, 25, domain);
  const domainsQuery = useDomainsList(true);
  const rescan = useRescanConflicts();
  const exportAll = useConflictsExportAll(domain);

  const today = new Date().toISOString().slice(0, 10);
  const from7d = new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
  const failuresQuery = useExecutionsList({
    page: 1, size: 50, integrity_status: 'FAILED', from: from7d, to: today,
  });

  const handleExport = async () => {
    const result = await exportAll.refetch();
    if (result.data?.data?.length) {
      downloadCsv(result.data.data, 'zamboni_dual_optimizer_report.csv');
    } else {
      message.info('No conflicted tables to export.');
    }
  };

  return (
    <div>
      <div style={{ color: '#667085', fontSize: 13, marginBottom: 16 }}>
        Tables where Zamboni HK is enabled AND an AWS Glue table optimizer (compaction /
        retention / orphan-file deletion) is also active on the same table. Gate 0 refuses to
        run Zamboni maintenance on these until the conflict clears or a time-boxed override is
        granted.
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={5}>
          <Statistic title="HK-Enabled Tables" value={conflicts.total} />
        </Col>
        <Col span={5}>
          <Statistic title="Optimizer-Scanned" value={conflicts.scanned} />
        </Col>
        <Col span={5}>
          <Statistic
            title="⚠️ Conflicted"
            value={conflicts.conflicted}
            valueStyle={conflicts.conflicted > 0 ? { color: '#B42318' } : undefined}
          />
        </Col>
        <Col span={4}>
          <Statistic title="Stale Cache" value={conflicts.stale_cache} />
        </Col>
        <Col span={5}>
          <Statistic title="Overridden" value={conflicts.overridden} />
        </Col>
      </Row>

      <DataGrid<ConflictRow>
        columns={CONFLICT_COLUMNS}
        queryResult={conflictsQuery}
        rowKey="table_fqn"
        emptyText="No dual-optimizer conflicts detected."
        onPageChange={setPage}
        toolbar={
          <Row justify="space-between" align="middle">
            <Col>
              <Select
                allowClear
                placeholder="Filter by domain"
                style={{ width: 200 }}
                value={domain}
                onChange={setDomain}
                options={(domainsQuery.data ?? []).map((d) => ({
                  value: d.domain_name, label: d.display_name || d.domain_name,
                }))}
              />
            </Col>
            <Col style={{ display: 'flex', gap: 8 }}>
              <Button
                loading={rescan.isPending}
                onClick={() =>
                  rescan.mutate(undefined, {
                    onSuccess: (r) => message.success(`Scanned ${r.scanned} table(s), found ${r.conflicts} conflict(s).`),
                  })
                }
              >
                🔄 Rescan conflicts
              </Button>
              <Button loading={exportAll.isFetching} onClick={handleExport}>
                ⬇️ Export CSV
              </Button>
            </Col>
          </Row>
        }
      />

      <div style={{ marginTop: 24, marginBottom: 8, fontWeight: 600, fontSize: 13 }}>
        Recent Integrity Failures — Last 7 Days
      </div>
      <DataGrid
        columns={FAILURE_COLUMNS}
        queryResult={failuresQuery}
        rowKey="execution_id"
        emptyText="No integrity failures in the last 7 days."
      />
    </div>
  );
}
