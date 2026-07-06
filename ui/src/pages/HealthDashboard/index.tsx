import { Alert, Button, Card, Col, Row, Skeleton, Statistic } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { CostTrendChart } from './components/CostTrendChart';
import { DryRunAdoptionTable } from './components/DryRunAdoptionTable';
import { FleetHealthScorecard } from './components/FleetHealthScorecard';
import { GovernanceSection } from './components/GovernanceSection';
import { NonprodFunnelChart } from './components/NonprodFunnelChart';
import { ReclaimedStorageTrendChart } from './components/ReclaimedStorageTrendChart';
import { TopReclaimChart } from './components/TopReclaimChart';
import { UnhealthyTablesGrid } from './components/UnhealthyTablesGrid';
import { useHealthDashboardData } from './hooks';

export default function HealthDashboardPage() {
  const { kpis } = useHealthDashboardData();

  if (kpis.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load Health Dashboard data"
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
  const loading = kpis.isLoading;

  return (
    <div>
      <PageHeader title="Health Dashboard" subtitle="Fleet-wide operational deep dive" />

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={16}>
          <Card size="small" title="Storage Reclaimed — Last 30 Days" style={{ height: '100%' }}>
            {loading ? <Skeleton active /> : <ReclaimedStorageTrendChart data={k?.reclaimed_storage_trend ?? []} />}
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="Estimated Savings" style={{ height: '100%' }}>
            {loading ? (
              <Skeleton active paragraph={{ rows: 2 }} />
            ) : (
              <>
                <Statistic
                  title="Total GB Reclaimed (all-time)"
                  value={k?.storage_savings.total_gb_reclaimed ?? 0}
                  suffix="GB"
                  valueStyle={{ color: '#167D9A', fontWeight: 700 }}
                />
                <Statistic
                  title="Est. Monthly Storage Savings"
                  value={k?.storage_savings.estimated_monthly_savings_usd ?? 0}
                  prefix="$"
                  valueStyle={{ color: '#027A48', fontWeight: 700, marginTop: 12 }}
                  style={{ marginTop: 16 }}
                />
              </>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card size="small" title="Top Tables by Storage Reclaimed" style={{ height: '100%' }}>
            {loading ? <Skeleton active /> : <TopReclaimChart data={k?.top_tables_by_reclaim ?? []} />}
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="Estimated Athena Cost — Last 30 Days" style={{ height: '100%' }}>
            {loading ? <Skeleton active /> : <CostTrendChart data={k?.cost_trend ?? []} />}
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card size="small" title="Fleet Health Scorecard" style={{ height: '100%' }}>
            {loading ? <Skeleton active /> : <FleetHealthScorecard data={k?.fleet_health ?? { healthy: 0, needs_attention: 0, at_risk: 0, tables: [] }} />}
          </Card>
        </Col>
        <Col span={16}>
          <Card size="small" title="Tables Needing Attention" style={{ height: '100%' }}>
            {loading ? <Skeleton active /> : <UnhealthyTablesGrid data={k?.fleet_health.tables ?? []} />}
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Card size="small" title="Non-Prod Lifecycle Funnel" style={{ height: '100%' }}>
            {loading ? <Skeleton active /> : <NonprodFunnelChart data={k?.nonprod_funnel ?? []} />}
          </Card>
        </Col>
        <Col span={12}>
          <Card
            size="small"
            title="Dry-Run Adoption — Longest Waiting by Domain"
            style={{ height: '100%' }}
          >
            {loading ? <Skeleton active /> : <DryRunAdoptionTable data={k?.dry_run_adoption ?? []} />}
          </Card>
        </Col>
      </Row>

      <Card size="small" title="🛡️ Maintenance Governance" style={{ marginTop: 16 }}>
        {loading ? (
          <Skeleton active />
        ) : (
          <GovernanceSection conflicts={k?.conflicts ?? { total: 0, scanned: 0, conflicted: 0, stale_cache: 0, overridden: 0 }} />
        )}
      </Card>
    </div>
  );
}
