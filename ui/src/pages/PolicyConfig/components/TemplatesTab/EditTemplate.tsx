import { Alert, Button, Card, Col, Form, Input, InputNumber, message, Row, Select } from 'antd';
import { useEffect, useState } from 'react';
import { useTemplates, useUpdateTemplate } from '../../../../api/hooks/usePolicies';
import type { WindowConfig } from '../../../../api/types';
import { GatesEditor, type GatesValue } from '../../../../components/GatesEditor';
import { WindowBlackoutEditor } from '../../../../components/WindowBlackoutEditor';

const DEFAULT_WINDOW: WindowConfig = {
  type: 'post_batch', timezone: 'America/Los_Angeles', delay_minutes: 30,
  start_time: '02:00', duration_hours: 4, blackout_hours: [6, 7, 8, 9, 18, 19, 20, 21],
};

const HHMM_RE = /^([01]?\d|2[0-3]):[0-5]\d$/;

/** Edit Template sub-tab (3_Policy_Configuration.py tmpl_tab_edit). */
export function EditTemplate() {
  const [name, setName] = useState<string | null>(null);
  const [form] = Form.useForm();
  const [gates, setGates] = useState<GatesValue>({ gate1_enabled: false, gate2_enabled: true, gate3_enabled: true });
  const [windowCfg, setWindowCfg] = useState<WindowConfig>(DEFAULT_WINDOW);

  const templates = useTemplates();
  const update = useUpdateTemplate();
  const tpl = name ? templates.data?.[name] : undefined;
  const strategy = Form.useWatch('compaction_strategy', form);
  const engineChoice = Form.useWatch('compaction_engine', form);
  const strategyEngineConflict = ['sort', 'zorder'].includes(strategy) && engineChoice === 'athena';

  useEffect(() => {
    if (!tpl) return;
    form.setFieldsValue(tpl);
    setGates({
      gate1_enabled: !!tpl.gate1_enabled, gate2_enabled: tpl.gate2_enabled !== false, gate3_enabled: tpl.gate3_enabled !== false,
    });
    setWindowCfg({ ...DEFAULT_WINDOW, ...(tpl.window_config as WindowConfig) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const handleSave = async () => {
    if (!name) return;
    const values = await form.validateFields();

    // Real gap closed (2026-07-10): this form applies fleet-wide (to every
    // table the template is bulk-applied to), yet had zero cross-field
    // validation at all -- the single-table Edit Table tab
    // (PolicyConfig/EditTableTab.tsx) already checks both of these. A bad
    // template here has more blast radius than a single bad table edit,
    // not less, so it needs at least the same checks.
    const errors: string[] = [];
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
    if (errors.length > 0) {
      errors.forEach((e) => message.error(e));
      return;
    }

    update.mutate(
      {
        name,
        body: {
          ...values,
          gate1_enabled: gates.gate1_enabled, gate2_enabled: gates.gate2_enabled, gate3_enabled: gates.gate3_enabled,
          window_config: windowCfg,
          dry_run: false,
        },
      },
      {
        onSuccess: (r) => message.success(`✅ Template ${name} saved (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  return (
    <div>
      <Select
        style={{ width: 320, marginBottom: 16 }}
        placeholder="Select template to edit"
        value={name}
        onChange={setName}
        options={Object.keys(templates.data ?? {}).map((t) => ({ value: t, label: t }))}
        loading={templates.isLoading}
      />

      {tpl && (
        <>
          <Card size="small" title="⏰ Window & Blackout" style={{ marginBottom: 16 }}>
            <WindowBlackoutEditor value={windowCfg} onChange={setWindowCfg} />
          </Card>
          <Card size="small" title="🚦 Gate Enable / Disable" style={{ marginBottom: 16 }}>
            <GatesEditor value={gates} onChange={setGates} showOverride={false} />
          </Card>
          <Card size="small">
            <Form form={form} layout="vertical">
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item name="description" label="Description"><Input /></Form.Item>
                  <Form.Item name="compaction_strategy" label="Compaction Strategy" tooltip="sort/zorder require the Glue engine -- Athena only supports binpack.">
                    <Select options={['binpack', 'sort', 'zorder'].map((s) => ({ value: s, label: s }))} />
                  </Form.Item>
                  <Form.Item name="compaction_engine" label="Compaction Engine">
                    <Select options={['athena', 'glue'].map((e) => ({ value: e, label: e }))} />
                  </Form.Item>
                  {strategyEngineConflict && (
                    <Alert
                      type="error" showIcon style={{ marginBottom: 16 }}
                      message={`Strategy '${strategy}' requires the Glue engine -- Athena only supports 'binpack'. Change one before saving.`}
                    />
                  )}
                  <Form.Item name="compaction_target_file_size_mb" label="Target File Size (MB)" tooltip="Target output file size after compaction.">
                    <InputNumber min={64} max={10240} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="snapshot_retention_days" label="Snapshot Retention (days)" tooltip="How long Iceberg snapshots are kept before vacuum expires them.">
                    <InputNumber min={1} max={3650} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="snapshot_min_to_keep" label="Min Snapshots to Keep" tooltip="Floor on snapshot count vacuum will never go below, regardless of age.">
                    <InputNumber min={2} max={1000} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="orphan_file_retention_days" label="Orphan Retention (days)" tooltip="Orphan files younger than this are never deleted by vacuum.">
                    <InputNumber min={2} max={3650} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="run_frequency" label="Run Frequency">
                    <Select options={['every_trigger', 'daily', 'weekly', 'monthly'].map((f) => ({ value: f, label: f }))} />
                  </Form.Item>
                </Col>
              </Row>
              <Button type="primary" onClick={handleSave} loading={update.isPending}>💾 Save Template</Button>
            </Form>
          </Card>
        </>
      )}
    </div>
  );
}
