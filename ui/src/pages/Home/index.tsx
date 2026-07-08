import { Alert, Button, Card, Col, Row, Skeleton } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ExecutionRow } from '../../api/types';
import { kpiCardPalette } from '../../colors';
import { DataGrid } from '../../components/DataGrid';
import { GovernanceChip } from '../../components/GovernanceChip';
import { KpiCard } from '../../components/KpiCard';
import { PageHeader } from '../../components/PageHeader';
import { StateBadge } from '../../components/StateBadge';
import { CoverageByDomainChart } from './components/CoverageByDomainChart';
import { CoverageDetailModal } from './components/CoverageDetailModal';
import { ExecutionsDetailModal } from './components/ExecutionsDetailModal';
import { ExecutionTrendChart } from './components/ExecutionTrendChart';
import './home.css';
import { useHomeData } from './hooks';

const EXECUTION_COLUMNS = [
  { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 170 },
  { title: 'Engine', dataIndex: 'engine', key: 'engine', width: 90 },
  { title: 'Operation', dataIndex: 'operation', key: 'operation', width: 120 },
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  {
    title: 'Status',
    dataIndex: 'status',
    key: 'status',
    width: 110,
    render: (status: string) => <StateBadge state={status} />,
  },
];

// Each KPI card opens the drill-down that actually explains its number:
// the first three are all coverage-shaped, the last two are execution lists.
const KPI_MODAL_TARGETS = ['coverage', 'coverage', 'coverage', 'execToday', 'failures'] as const;
type KpiModal = (typeof KPI_MODAL_TARGETS)[number] | null;

export default function HomePage() {
  const {
    kpis, locks,
    recentExecutions, onRecentPageChange,
    executionsToday, onExecutionsTodayPageChange,
    failures7d, onFailures7dPageChange,
  } = useHomeData();
  const [openModal, setOpenModal] = useState<KpiModal>(null);

  if (kpis.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load health KPIs"
        description={kpis.error instanceof Error ? kpis.error.message : String(kpis.error)}
        action={
          <Button size="small" danger onClick={() => kpis.refetch()}>
            Retry
          </Button>
        }
      />
    );
  }

  const k = kpis.data;
  const coveragePct = k && k.total_registered > 0 ? Math.round((k.hk_enabled / k.total_registered) * 1000) / 10 : 0;
  const execToday = executionsToday.data?.pagination?.total ?? 0;
  const activeLocks = locks.data?.length ?? 0;
  const conflicted = k?.conflicts.conflicted ?? 0;
  const governanceClear = !kpis.isLoading && conflicted === 0 && activeLocks === 0;

  // Mint = "all clear," matching the dry-run banner's safety convention;
  // amber = needs attention. Static mint regardless of state would say
  // "calm" while a red conflict chip sat inside it — see decisions.md.
  const governanceTone = kpis.isLoading
    ? { background: '#FFFFFF', border: '#D9E2EC', titleColor: '#172B4D' }
    : governanceClear
      ? { background: 'linear-gradient(90deg, #ECFDF3 0%, #F5FFF8 100%)', border: '#B7E6C6', titleColor: '#176B43' }
      : { background: 'linear-gradient(90deg, #FFFAEB 0%, #FFFDF6 100%)', border: '#FEDF89', titleColor: '#8A5B12' };

  const kpiValues = [
    k?.total_registered ?? 0,
    k?.hk_enabled ?? 0,
    coveragePct,
    execToday,
    k?.failures_7d ?? 0,
  ];
  const kpiSuffixes = [undefined, undefined, '%', undefined, undefined];
  const kpiLoading = [kpis.isLoading, kpis.isLoading, kpis.isLoading, executionsToday.isLoading, kpis.isLoading];

  return (
    <div>
      <PageHeader title="Home" subtitle="Fleet governance at a glance" />

      <div className="zamboni-kpi-grid">
        {kpiCardPalette.map((palette, i) => (
          <KpiCard
            key={palette.label}
            palette={palette}
            value={kpiValues[i]}
            suffix={kpiSuffixes[i]}
            loading={kpiLoading[i]}
            onClick={() => setOpenModal(KPI_MODAL_TARGETS[i])}
          />
        ))}
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card size="small" title="Fleet Coverage by Domain" style={{ height: '100%' }}>
            {kpis.isLoading ? (
              <Skeleton active />
            ) : (
              <CoverageByDomainChart data={k?.coverage_by_domain ?? []} />
            )}
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="Execution Trend — Last 14 Days" style={{ height: '100%' }}>
            {kpis.isLoading ? (
              <Skeleton active />
            ) : (
              <ExecutionTrendChart data={k?.execution_trend ?? []} />
            )}
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        style={{
          marginBottom: 16,
          background: governanceTone.background,
          border: `1px solid ${governanceTone.border}`,
        }}
        styles={{ body: { background: 'transparent' } }}
      >
        <Row align="middle" gutter={24}>
          <Col>
            <span style={{ fontWeight: 700, marginRight: 8, color: governanceTone.titleColor }}>Governance</span>
          </Col>
          <Col>
            {kpis.isLoading ? (
              <Skeleton.Input active size="small" style={{ width: 160 }} />
            ) : (
              <>
                <GovernanceChip
                  text={`${conflicted} conflicted table(s)`}
                  tone={conflicted > 0 ? 'error' : 'success'}
                />
                <Link to="/health" style={{ fontSize: 13, marginLeft: 12 }}>
                  view report →
                </Link>
              </>
            )}
          </Col>
          <Col>
            <GovernanceChip text={`${activeLocks} active lock(s)`} tone={activeLocks > 0 ? 'warning' : 'neutral'} />
          </Col>
        </Row>
      </Card>

      <Card size="small" title="Recent Activity">
        <DataGrid<ExecutionRow>
          columns={EXECUTION_COLUMNS}
          queryResult={recentExecutions}
          rowKey="execution_id"
          emptyText="No engine activity yet."
          onPageChange={onRecentPageChange}
        />
      </Card>

      <CoverageDetailModal
        open={openModal === 'coverage'}
        onClose={() => setOpenModal(null)}
        title="Fleet Coverage by Domain"
        data={k?.coverage_by_domain ?? []}
      />
      <ExecutionsDetailModal
        open={openModal === 'execToday'}
        onClose={() => setOpenModal(null)}
        title="Executions — Today"
        queryResult={executionsToday}
        emptyText="No executions yet today."
        onPageChange={onExecutionsTodayPageChange}
      />
      <ExecutionsDetailModal
        open={openModal === 'failures'}
        onClose={() => setOpenModal(null)}
        title="Failures — Last 7 Days"
        queryResult={failures7d}
        emptyText="No failures in the last 7 days."
        onPageChange={onFailures7dPageChange}
      />
    </div>
  );
}
