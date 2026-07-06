import { Button, Card, Col, Form, Input, InputNumber, message, Row, Select } from 'antd';
import { useEffect, useState } from 'react';
import { useTemplates, useUpdateTemplate } from '../../../../api/hooks/usePolicies';
import type { WindowConfig } from '../../../../api/types';
import { GatesEditor, type GatesValue } from '../../../../components/GatesEditor';
import { WindowBlackoutEditor } from '../../../../components/WindowBlackoutEditor';

const DEFAULT_WINDOW: WindowConfig = {
  type: 'post_batch', timezone: 'America/Los_Angeles', delay_minutes: 30,
  start_time: '02:00', duration_hours: 4, blackout_hours: [6, 7, 8, 9, 18, 19, 20, 21],
};

/** Edit Template sub-tab (3_Policy_Configuration.py tmpl_tab_edit). */
export function EditTemplate() {
  const [name, setName] = useState<string | null>(null);
  const [form] = Form.useForm();
  const [gates, setGates] = useState<GatesValue>({ gate1_enabled: false, gate2_enabled: true, gate3_enabled: true });
  const [windowCfg, setWindowCfg] = useState<WindowConfig>(DEFAULT_WINDOW);

  const templates = useTemplates();
  const update = useUpdateTemplate();
  const tpl = name ? templates.data?.[name] : undefined;

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
                  <Form.Item name="compaction_strategy" label="Compaction Strategy">
                    <Select options={['binpack', 'sort', 'zorder'].map((s) => ({ value: s, label: s }))} />
                  </Form.Item>
                  <Form.Item name="compaction_engine" label="Compaction Engine">
                    <Select options={['athena', 'glue'].map((e) => ({ value: e, label: e }))} />
                  </Form.Item>
                  <Form.Item name="compaction_target_file_size_mb" label="Target File Size (MB)">
                    <InputNumber min={64} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="snapshot_retention_days" label="Snapshot Retention (days)">
                    <InputNumber min={1} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="snapshot_min_to_keep" label="Min Snapshots to Keep">
                    <InputNumber min={2} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item name="orphan_file_retention_days" label="Orphan Retention (days)">
                    <InputNumber min={2} style={{ width: '100%' }} />
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
