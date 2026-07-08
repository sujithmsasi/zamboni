import { Button, Checkbox, Col, Divider, InputNumber, message, Row, Select, Skeleton } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useUpdateSettings } from '../../../api/hooks/useSettings';
import { useSettingsPageData } from '../hooks';

const REFRESH_OPTIONS = [10, 15, 30, 60, 120, 300];
const ENVS = ['prod', 'preprod', 'dev', 'test'];

export function GeneralTab() {
  const { settings } = useSettingsPageData();
  const updateSettings = useUpdateSettings();

  const [execRetention, setExecRetention] = useState(90);
  const [auditRetention, setAuditRetention] = useState(365);
  const [refreshInterval, setRefreshInterval] = useState(30);
  const [budgetThreshold, setBudgetThreshold] = useState(0);
  const [dryRunByEnv, setDryRunByEnv] = useState<Record<string, boolean>>({});

  // Applies once on first load only -- a background refetch (e.g. window
  // focus) must not clobber an in-progress edit before Save is clicked
  // (same reasoning as PolicyConfig/EditTableTab's fqn-keyed sync guard).
  const applied = useRef(false);
  useEffect(() => {
    if (!settings.data || applied.current) return;
    applied.current = true;
    setExecRetention(Number(settings.data.execution_log_retention_days ?? 90));
    setAuditRetention(Number(settings.data.audit_log_retention_days ?? 365));
    setRefreshInterval(Number(settings.data.live_activity_refresh_interval_seconds ?? 30));
    setBudgetThreshold(Number(settings.data.budget_alert_threshold_usd_monthly ?? 0));
    setDryRunByEnv({ prod: true, preprod: true, dev: true, test: true, ...settings.data.default_dry_run });
  }, [settings.data]);

  const handleSave = () => {
    updateSettings.mutate(
      {
        settings: {
          execution_log_retention_days: execRetention,
          audit_log_retention_days: auditRetention,
          live_activity_refresh_interval_seconds: refreshInterval,
          budget_alert_threshold_usd_monthly: budgetThreshold,
          default_dry_run: dryRunByEnv,
        },
        dry_run: false,
      },
      {
        onSuccess: (r) => message.success(`✅ General settings saved (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  if (settings.isLoading) return <Skeleton active />;

  return (
    <div style={{ maxWidth: 640 }}>
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Retention</div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Execution Log Retention (days)</div>
          <InputNumber style={{ width: '100%' }} min={7} max={3650} value={execRetention} onChange={(v) => setExecRetention(v ?? 90)} />
        </Col>
        <Col span={12}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Audit Log Retention (days)</div>
          <InputNumber style={{ width: '100%' }} min={30} max={3650} value={auditRetention} onChange={(v) => setAuditRetention(v ?? 365)} />
        </Col>
      </Row>

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Live Activity</div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Default Refresh Interval (seconds)</div>
        <Select style={{ width: 200 }} value={refreshInterval} onChange={setRefreshInterval} options={REFRESH_OPTIONS.map((s) => ({ value: s, label: `${s}s` }))} />
      </div>

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Budget Alert</div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Monthly Athena Budget Threshold (USD, 0 = disabled)</div>
        <InputNumber style={{ width: 260 }} min={0} max={100000} value={budgetThreshold} onChange={(v) => setBudgetThreshold(v ?? 0)} />
      </div>

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Default Dry-Run Mode per Environment</div>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        {ENVS.map((env) => (
          <Col span={6} key={env}>
            <Checkbox
              checked={!!dryRunByEnv[env]}
              onChange={(e) => setDryRunByEnv((prev) => ({ ...prev, [env]: e.target.checked }))}
            >
              {env}
            </Checkbox>
          </Col>
        ))}
      </Row>

      <Button type="primary" loading={updateSettings.isPending} onClick={handleSave}>
        💾 Save General Settings
      </Button>
    </div>
  );
}
