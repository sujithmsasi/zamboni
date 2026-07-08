import { Alert, Button, Col, Collapse, Form, Input, InputNumber, message, Row, Select, Skeleton, Tag } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { usePolicyDetail, useUpdatePolicy } from '../../../api/hooks/usePolicies';
import { useTablesSearch } from '../../../api/hooks/useTables';
import { useUpdateGates } from '../../../api/hooks/useGates';
import { useSystemMode } from '../../../api/hooks/useSystem';
import type { WindowConfig } from '../../../api/types';
import { GatesEditor, type GatesValue } from '../../../components/GatesEditor';
import { WindowBlackoutEditor } from '../../../components/WindowBlackoutEditor';

const PARTITION_TYPES = ['date', 'timestamp', 'int_yyyymmdd', 'string', 'identity', 'none'];
const RUN_FREQUENCIES = ['every_trigger', 'daily', 'weekly', 'monthly'];

const DEFAULT_WINDOW: WindowConfig = {
  type: 'post_batch', timezone: 'America/Los_Angeles', delay_minutes: 30,
  start_time: '02:00', duration_hours: 4, blackout_hours: [6, 7, 8, 9, 18, 19, 20, 21],
};

const HHMM_RE = /^([01]?\d|2[0-3]):[0-5]\d$/;

/** Edit Single Table tab (3_Policy_Configuration.py tab_edit). */
export function EditTableTab() {
  const [fqn, setFqn] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [form] = Form.useForm();
  const [gates, setGates] = useState<GatesValue>({ gate1_enabled: false, gate2_enabled: true, gate3_enabled: true });
  const [windowCfg, setWindowCfg] = useState<WindowConfig>(DEFAULT_WINDOW);
  const [reason, setReason] = useState('');
  const [activeKey, setActiveKey] = useState('gates');

  const tables = useTablesSearch(search);
  const detail = usePolicyDetail(fqn);
  const systemMode = useSystemMode();
  const updatePolicy = useUpdatePolicy();
  const updateGates = useUpdateGates();
  const compactionStrategy = Form.useWatch('compaction_strategy', form);

  // Applies server data into the local edit state exactly once per table
  // selection -- keying the effect on `detail.data`'s object identity
  // instead of `fqn` would re-clobber in-progress edits (gates/window
  // toggles, the reason field) on every background refetch of the same
  // table (e.g. TanStack Query's default refetchOnWindowFocus), silently
  // discarding unsaved changes right before the user clicks Save.
  const appliedForFqn = useRef<string | null>(null);
  useEffect(() => {
    if (!detail.data || appliedForFqn.current === fqn) return;
    appliedForFqn.current = fqn;
    form.setFieldsValue(detail.data);
    setGates({
      gate1_enabled: !!detail.data.gate1_enabled,
      gate2_enabled: !!detail.data.gate2_enabled,
      gate3_enabled: !!detail.data.gate3_enabled,
    });
    let parsed: WindowConfig = DEFAULT_WINDOW;
    if (detail.data.window_config) {
      try { parsed = { ...DEFAULT_WINDOW, ...JSON.parse(detail.data.window_config) }; } catch { /* keep default */ }
    }
    setWindowCfg(parsed);
    setReason('');
    setActiveKey('gates');
  }, [detail.data, fqn, form]);

  const handleSave = async () => {
    if (!fqn) return;
    const values = await form.validateFields();

    const errors: string[] = [];
    if (!reason.trim()) errors.push('Reason for override is required.');
    if (values.snapshot_retention_days < 1) errors.push('Snapshot Retention must be at least 1 day.');
    if (values.snapshot_min_to_keep < 2) errors.push('Min Snapshots to Keep must be at least 2.');
    if (values.orphan_file_retention_days < 2) errors.push('Orphan Retention must be at least 2 days.');
    if (values.compaction_target_file_size_mb < 64) errors.push('Target File Size must be at least 64 MB.');
    if (['sort', 'zorder'].includes(values.compaction_strategy) && values.compaction_engine === 'athena') {
      errors.push(`Strategy '${values.compaction_strategy}' requires Glue engine. Athena only supports 'binpack'.`);
    }
    if (windowCfg.type === 'scheduled') {
      const st = windowCfg.start_time.trim();
      if (!st) errors.push('Start time is required for scheduled windows.');
      else if (!HHMM_RE.test(st)) errors.push(`Start time '${st}' is not valid HH:MM (24h format).`);
      else if (windowCfg.blackout_hours.includes(Number(st.split(':')[0]))) {
        errors.push(`Start time ${st} falls in a blackout hour. Change start time or uncheck that blackout hour.`);
      }
    }
    if (gates.gate0_override_until && !gates.gate0_override_reason?.trim()) {
      errors.push('A reason is required to set a Gate 0 override.');
    }
    if (errors.length > 0) {
      errors.forEach((e) => message.error(e));
      return;
    }

    try {
      const r1 = await updatePolicy.mutateAsync({
        fqn,
        body: {
          snapshot_retention_days: values.snapshot_retention_days,
          snapshot_min_to_keep: values.snapshot_min_to_keep,
          orphan_file_retention_days: values.orphan_file_retention_days,
          orphan_cleanup_cadence_days: values.orphan_cleanup_cadence_days,
          run_frequency: values.run_frequency,
          compaction_strategy: values.compaction_strategy,
          compaction_engine: values.compaction_engine,
          compaction_target_file_size_mb: values.compaction_target_file_size_mb,
          partition_column: values.partition_column,
          partition_type: values.partition_type,
          sort_order_cols: values.sort_order_cols,
          window_config: windowCfg,
          override_notes: reason,
          dry_run: false,
        },
      });
      const gatesBody: Record<string, unknown> = {
        gate1_enabled: gates.gate1_enabled, gate2_enabled: gates.gate2_enabled, gate3_enabled: gates.gate3_enabled,
        dry_run: false,
      };
      if (gates.gate0_override_until) {
        gatesBody.gate0_override_until = gates.gate0_override_until;
        gatesBody.gate0_override_reason = gates.gate0_override_reason;
      }
      const r2 = await updateGates.mutateAsync({ fqn, body: gatesBody });
      message.success(`✅ Config saved for ${fqn} (audit: ${r1.audit_id}, ${r2.audit_id}).`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Save failed.');
    }
  };

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Table (type to search)</div>
        <Select
          showSearch style={{ width: 420 }} placeholder="Select a table"
          value={fqn} onChange={setFqn} onSearch={setSearch} filterOption={false}
          options={(tables.data?.data ?? []).map((t) => ({ value: t.table_fqn, label: t.table_fqn }))}
          notFoundContent={tables.isLoading ? 'Searching…' : 'Type to search'}
        />
      </div>

      {!fqn && <Alert type="info" showIcon message="Select a table above to edit its HK config." />}
      {fqn && detail.isLoading && <Skeleton active />}
      {fqn && detail.isError && (
        <Alert
          type="warning" showIcon
          message="No HK config found for this table. Apply a template in the Bulk Apply tab first."
        />
      )}

      {fqn && detail.data && (
        <>
          <div style={{ marginBottom: 12, fontSize: 13, color: '#667085' }}>
            Template: <strong>{detail.data.policy_template ?? '—'}</strong> ·{' '}
            {detail.data.manually_overridden ? '⚠️ Manually overridden' : '✅ On template'}
          </div>

          <Collapse
            accordion
            activeKey={activeKey}
            onChange={(k) => setActiveKey(Array.isArray(k) ? k[0] ?? '' : k)}
            style={{ marginBottom: 16 }}
            items={[
              {
                key: 'gates',
                label: '🚦 Gate Enable / Disable',
                extra: (() => {
                  const active = [gates.gate1_enabled, gates.gate2_enabled, gates.gate3_enabled].filter(Boolean).length;
                  return <Tag color={active === 3 ? 'green' : 'gold'}>{active}/3 active</Tag>;
                })(),
                children: <GatesEditor value={gates} onChange={setGates} overrideMaxHours={systemMode.data?.gate0_override_max_hours ?? 24} />,
              },
              {
                key: 'window',
                label: '⏰ Safe Window & Blackout',
                extra: <Tag>{windowCfg.type} · {windowCfg.blackout_hours.length}h blocked</Tag>,
                children: <WindowBlackoutEditor value={windowCfg} onChange={setWindowCfg} />,
              },
              {
                key: 'compaction',
                label: 'Compaction & Snapshot Settings',
                extra: <Tag>{compactionStrategy ?? detail.data.compaction_strategy}</Tag>,
                children: (
                  <Form form={form} layout="vertical">
                    <Row gutter={16}>
                      <Col span={8}>
                        <Form.Item name="snapshot_retention_days" label="Snapshot Retention (days) *">
                          <InputNumber min={1} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="snapshot_min_to_keep" label="Min Snapshots to Keep *">
                          <InputNumber min={2} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="run_frequency" label="Run Frequency *">
                          <Select options={RUN_FREQUENCIES.map((f) => ({ value: f, label: f }))} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={16}>
                      <Col span={8}>
                        <Form.Item name="orphan_file_retention_days" label="Orphan Retention (days) *">
                          <InputNumber min={2} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="orphan_cleanup_cadence_days" label="Orphan Cleanup Cadence (days)">
                          <InputNumber min={0} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="compaction_target_file_size_mb" label="Target File Size (MB) *">
                          <InputNumber min={64} style={{ width: '100%' }} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={16}>
                      <Col span={8}>
                        <Form.Item name="compaction_strategy" label="Compaction Strategy *">
                          <Select options={['binpack', 'sort', 'zorder'].map((s) => ({ value: s, label: s }))} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="compaction_engine" label="Compaction Engine *">
                          <Select options={['athena', 'glue'].map((e) => ({ value: e, label: e }))} />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="partition_type" label="Partition Type">
                          <Select options={PARTITION_TYPES.map((p) => ({ value: p, label: p }))} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={16}>
                      <Col span={12}>
                        <Form.Item name="partition_column" label="Partition Column">
                          <Input placeholder="partition_date" />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item name="sort_order_cols" label="Sort / Z-Order Columns">
                          <Input placeholder="partition_date, customer_id" />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Form>
                ),
              },
            ]}
          />

          <div
            style={{
              position: 'sticky',
              bottom: 0,
              zIndex: 10,
              background: '#fff',
              borderTop: '1px solid #D9E2EC',
              borderRadius: 8,
              padding: '12px 16px',
              boxShadow: '0 -2px 8px rgba(0,0,0,0.06)',
            }}
          >
            <Row gutter={16} align="middle">
              <Col flex="auto">
                <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Reason for override *</div>
                <Input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. High-volume table needs shorter retention"
                />
              </Col>
              <Col>
                <Button type="primary" onClick={handleSave} loading={updatePolicy.isPending || updateGates.isPending}>
                  💾 Save Changes
                </Button>
              </Col>
            </Row>
          </div>
        </>
      )}
    </div>
  );
}
