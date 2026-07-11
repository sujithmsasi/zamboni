import { Alert, Button, Col, Form, Input, InputNumber, message, Row, Select } from 'antd';
import { useCreateTemplate } from '../../../../api/hooks/usePolicies';

// Matches the tooltip's promised convention exactly (uppercase + underscores,
// no spaces) -- the previous version only uppercased, so "custom high volume"
// silently saved as "CUSTOM HIGH VOLUME" (spaces intact) with no warning.
function normalizeTemplateName(raw: string): string {
  return raw.trim().toUpperCase().replace(/\s+/g, '_');
}

/** Add Template sub-tab (3_Policy_Configuration.py tmpl_tab_add). */
export function AddTemplate() {
  const [form] = Form.useForm();
  const create = useCreateTemplate();
  const strategy = Form.useWatch('compaction_strategy', form);
  const engineChoice = Form.useWatch('compaction_engine', form);
  const strategyEngineConflict = ['sort', 'zorder'].includes(strategy) && engineChoice === 'athena';

  const handleSubmit = async () => {
    const values = await form.validateFields();

    const errors: string[] = [];
    if (['sort', 'zorder'].includes(values.compaction_strategy) && values.compaction_engine === 'athena') {
      errors.push(`Strategy '${values.compaction_strategy}' requires Glue engine. Athena only supports 'binpack'.`);
    }
    if (errors.length > 0) {
      errors.forEach((e) => message.error(e));
      return;
    }

    const name = normalizeTemplateName(values.name);
    if (name !== values.name.trim()) {
      message.info(`Template name adjusted to '${name}' to match the naming convention.`);
    }
    create.mutate(
      { ...values, name, dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ Template ${name} added (audit: ${r.audit_id}).`);
          form.resetFields();
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Failed to add template.'),
      },
    );
  };

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        compaction_strategy: 'binpack', compaction_engine: 'athena', compaction_target_file_size_mb: 256,
        run_frequency: 'daily', snapshot_retention_days: 7, snapshot_min_to_keep: 30, orphan_file_retention_days: 3,
      }}
    >
      <Form.Item
        name="name" label="Template Name"
        rules={[{ required: true, message: 'Template name is required' }]}
        tooltip="Uppercase, underscores. This becomes the key in policy_templates.json."
      >
        <Input placeholder="CUSTOM_HIGH_VOLUME" />
      </Form.Item>
      <Form.Item name="description" label="Description">
        <Input.TextArea rows={2} placeholder="High-volume tables with hourly CDC" />
      </Form.Item>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item name="compaction_strategy" label="Strategy" tooltip="sort/zorder require the Glue engine -- Athena only supports binpack.">
            <Select options={['binpack', 'sort', 'zorder'].map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="compaction_engine" label="Engine">
            <Select options={['athena', 'glue'].map((e) => ({ value: e, label: e }))} />
          </Form.Item>
          {strategyEngineConflict && (
            <Alert
              type="error" showIcon style={{ marginBottom: 16 }}
              message={`Strategy '${strategy}' requires the Glue engine -- Athena only supports 'binpack'. Change one before saving.`}
            />
          )}
          <Form.Item name="compaction_target_file_size_mb" label="Target MB" tooltip="Target output file size after compaction.">
            <InputNumber min={64} max={10240} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="run_frequency" label="Frequency">
            <Select options={['every_trigger', 'daily', 'weekly', 'monthly'].map((f) => ({ value: f, label: f }))} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="snapshot_retention_days" label="Snapshot Retention (days)" tooltip="How long Iceberg snapshots are kept before vacuum expires them.">
            <InputNumber min={1} max={3650} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="snapshot_min_to_keep" label="Min Snapshots" tooltip="Floor on snapshot count vacuum will never go below, regardless of age.">
            <InputNumber min={2} max={1000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="orphan_file_retention_days" label="Orphan Retention (days)" tooltip="Orphan files younger than this are never deleted by vacuum.">
            <InputNumber min={2} max={3650} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>
      <Button type="primary" onClick={handleSubmit} loading={create.isPending}>➕ Add Template</Button>
    </Form>
  );
}
