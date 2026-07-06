import { Button, Col, Form, Input, InputNumber, message, Row, Select } from 'antd';
import { useCreateTemplate } from '../../../../api/hooks/usePolicies';

/** Add Template sub-tab (3_Policy_Configuration.py tmpl_tab_add). */
export function AddTemplate() {
  const [form] = Form.useForm();
  const create = useCreateTemplate();

  const handleSubmit = async () => {
    const values = await form.validateFields();
    create.mutate(
      { ...values, name: values.name.trim().toUpperCase(), dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ Template ${values.name.trim().toUpperCase()} added (audit: ${r.audit_id}).`);
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
          <Form.Item name="compaction_strategy" label="Strategy">
            <Select options={['binpack', 'sort', 'zorder'].map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="compaction_engine" label="Engine">
            <Select options={['athena', 'glue'].map((e) => ({ value: e, label: e }))} />
          </Form.Item>
          <Form.Item name="compaction_target_file_size_mb" label="Target MB">
            <InputNumber min={64} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="run_frequency" label="Frequency">
            <Select options={['every_trigger', 'daily', 'weekly', 'monthly'].map((f) => ({ value: f, label: f }))} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="snapshot_retention_days" label="Snapshot Retention (days)">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="snapshot_min_to_keep" label="Min Snapshots">
            <InputNumber min={2} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="orphan_file_retention_days" label="Orphan Retention (days)">
            <InputNumber min={2} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>
      <Button type="primary" onClick={handleSubmit} loading={create.isPending}>➕ Add Template</Button>
    </Form>
  );
}
