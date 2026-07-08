import { Alert, Button, Card, Col, Descriptions, message, Row, Select, Skeleton, Statistic } from 'antd';
import { useState } from 'react';
import { GovernanceChip } from '../../components/GovernanceChip';
import { PageHeader } from '../../components/PageHeader';
import { useDryRunViewerData } from './hooks';

// Not ported from app/pages/6_Dry_Run_Viewer.py:
//  - "Promote to Live" (reason/ticket form + audit) -- contracts.md §6 has
//    no POST endpoint for it, only GET /api/dryrun/{fqn}; adding one is out
//    of scope for this wave (unlike domains, the phase brief didn't call
//    for adding a promote route here).
//  - "Domain Dry Run" bulk-simulate tab -- same reason, no domain-level
//    dry-run endpoint exists.
export default function DryRunViewerPage() {
  const [search, setSearch] = useState('');
  const [fqn, setFqn] = useState<string | null>(null);
  const { tables, dryRun } = useDryRunViewerData(search, fqn);

  const options = (tables.data?.data ?? []).map((t) => ({ value: t.table_fqn, label: t.table_fqn }));

  const handleCopySql = async () => {
    if (!dryRun.data?.planned_sql) return;
    await navigator.clipboard.writeText(dryRun.data.planned_sql);
    message.success('SQL copied to clipboard.');
  };

  return (
    <div>
      <PageHeader title="Dry Run Viewer" subtitle="Simulate the HK Engine on any table without writing anything" />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Select
          showSearch
          allowClear
          style={{ width: 420 }}
          placeholder="Type table name or database to search"
          value={fqn}
          filterOption={false}
          onSearch={setSearch}
          onChange={setFqn}
          options={options}
          notFoundContent={tables.isLoading ? 'Searching…' : search ? 'No matching tables' : 'Type to search'}
        />
      </Card>

      {!fqn && <Alert type="info" showIcon message="Select a table above to run a dry run." />}

      {fqn && dryRun.isLoading && <Skeleton active />}

      {fqn && dryRun.isError && (
        <Alert
          type="error" showIcon message={`Table '${fqn}' is not registered, or the dry run failed.`}
          action={<Button size="small" danger onClick={() => dryRun.refetch()}>Retry</Button>}
        />
      )}

      {dryRun.data && (
        <>
          <Alert type="success" showIcon style={{ marginBottom: 16 }} message={`Dry run complete for ${dryRun.data.table_fqn}`} />

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Card size="small"><Statistic title="Domain" value={String(dryRun.data.registration.domain ?? '—')} /></Card>
            </Col>
            <Col span={8}>
              <Card size="small"><Statistic title="Layer" value={String(dryRun.data.registration.layer ?? '—')} /></Card>
            </Col>
            <Col span={8}>
              <Card size="small"><Statistic title="Tier" value={String(dryRun.data.registration.tier ?? '—')} /></Card>
            </Col>
          </Row>

          <Card size="small" title="⏰ Window Evaluation" style={{ marginBottom: 16 }}>
            <GovernanceChip
              text={`Window: ${dryRun.data.gates.window_decision}`}
              tone={dryRun.data.gates.window_decision === 'EXECUTE' ? 'success' : 'warning'}
            />
          </Card>

          <Card size="small" title="🚦 Gate Summary" style={{ marginBottom: 16 }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="Gate 1 — Control-M upstream check">
                <GovernanceChip
                  text={
                    dryRun.data.gates.gate1_enabled
                      ? `Enabled${dryRun.data.gates.upstream_job ? ` — Job: ${dryRun.data.gates.upstream_job}` : ''}`
                      : 'Disabled'
                  }
                  tone={dryRun.data.gates.gate1_enabled ? 'success' : 'warning'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="Gate 2 — Safe window">
                <GovernanceChip
                  text={
                    dryRun.data.gates.gate2_enabled
                      ? `Enabled — Decision: ${dryRun.data.gates.window_decision}`
                      : 'Disabled — HK will run in any hour'
                  }
                  tone={dryRun.data.gates.gate2_enabled ? 'success' : 'warning'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="Gate 3 — Circuit breaker">
                <GovernanceChip
                  text={dryRun.data.gates.gate3_enabled ? 'Enabled — would check failure count' : 'Disabled — circuit breaker bypassed'}
                  tone={dryRun.data.gates.gate3_enabled ? 'success' : 'warning'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="Strategy">
                {String(dryRun.data.config.compaction_strategy ?? '—')}
              </Descriptions.Item>
            </Descriptions>
          </Card>

          {dryRun.data.planned_sql && (
            <Card
              size="small" title="🔧 Compaction SQL Preview"
              extra={<Button size="small" onClick={handleCopySql}>Copy SQL</Button>}
            >
              <pre style={{ background: '#F7F9FC', padding: 12, borderRadius: 6, fontSize: 12, overflowX: 'auto' }}>
                {dryRun.data.planned_sql}
              </pre>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
