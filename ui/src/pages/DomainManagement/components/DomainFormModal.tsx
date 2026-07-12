import { Checkbox, Col, Descriptions, Form, Input, InputNumber, message, Modal, Row, Space } from 'antd';
import { useEffect } from 'react';
import { useCreateDomain, useUpdateDomain } from '../../../api/hooks/useDomains';
import type { DomainRow } from '../../../api/types';

interface DomainFormModalProps {
  open: boolean;
  domain: DomainRow | null; // null = register mode
  onClose: () => void;
}

const CREATE_DEFAULTS = {
  archive_enabled: true,
  is_active: true,
  hot_retention_days: 30,
  archive_duration_days: 365,
  stale_threshold_days: 60,
  auto_delete_after_days: 120,
};

/** CRUD modal for domains — register (no domain param) or edit (prefilled). */
export function DomainFormModal({ open, domain, onClose }: DomainFormModalProps) {
  const [form] = Form.useForm();
  const create = useCreateDomain();
  const update = useUpdateDomain();
  const isEdit = !!domain;

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    if (domain) form.setFieldsValue(domain);
  }, [open, domain, form]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    if (isEdit && domain) {
      update.mutate(
        { name: domain.domain_name, body: { ...values, dry_run: false } },
        {
          onSuccess: (r) => {
            message.success(`Domain ${domain.domain_name} updated (audit: ${r.audit_id}).`);
            onClose();
          },
          onError: (err) => message.error(err instanceof Error ? err.message : 'Update failed.'),
        },
      );
    } else {
      create.mutate(
        { ...values, dry_run: false },
        {
          onSuccess: (r) => {
            message.success(`Domain ${values.domain_name} registered (audit: ${r.audit_id}).`);
            onClose();
          },
          onError: (err) => message.error(err instanceof Error ? err.message : 'Registration failed.'),
        },
      );
    }
  };

  return (
    <Modal
      open={open}
      destroyOnHidden
      title={isEdit ? `Edit Domain — ${domain?.domain_name}` : 'Register New Domain'}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={create.isPending || update.isPending}
      width={880}
      okText={isEdit ? 'Update Domain' : 'Register Domain'}
    >
      <Form form={form} layout="vertical" initialValues={CREATE_DEFAULTS}>
        <Row gutter={24}>
          <Col span={12}>
            {!isEdit && (
              <Form.Item name="domain_name" label="Domain Name" rules={[{ required: true }]} help="Lowercase, no spaces — e.g. finance, ers, membership">
                <Input placeholder="finance" />
              </Form.Item>
            )}
            <Form.Item name="display_name" label="Display Name" rules={[{ required: true }]}>
              <Input placeholder="Finance" />
            </Form.Item>
            <Form.Item name="owner_name" label="Owner Name">
              <Input placeholder="John Smith" />
            </Form.Item>
            <Form.Item
              name="owner_email"
              label="Owner Email"
              rules={[
                { required: true, message: 'Owner email is required' },
                { type: 'email', message: 'Enter a valid email address' },
              ]}
              tooltip="Default digest/notification recipient for this domain -- overridable per-table via Digest Email below."
            >
              <Input placeholder="da-finance@company.com" />
            </Form.Item>
            <Form.Item name="team_name" label="Team">
              <Input placeholder="Data & Analytics - Finance" />
            </Form.Item>
            <Form.Item name="ci_number" label="CI Number">
              <Input placeholder="CI-10234" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="hot_retention_days" label="Hot Retention (days)"
              rules={[{ required: true, type: 'number', min: 1, max: 3650, message: 'Enter a whole number between 1 and 3650' }]}
              tooltip="How long staging data stays in S3 Standard before archival. Typical: Finance=30, ERS=7, Claims=90."
            >
              <InputNumber min={1} max={3650} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="archive_duration_days" label="Archive Duration (days)"
              rules={[{ required: true, type: 'number', min: 1, max: 3650, message: 'Enter a whole number between 1 and 3650' }]}
              tooltip="How long archived data is kept in S3 Intelligent-Tiering before it can be deleted."
            >
              <InputNumber min={1} max={3650} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="stale_threshold_days" label="Stale Threshold (days)"
              rules={[{ required: true, type: 'number', min: 7, max: 3650, message: 'Enter a whole number between 7 and 3650' }]}
              tooltip="Non-prod tables inactive beyond this threshold are flagged as STALE_CANDIDATE by the Lifecycle Engine."
            >
              <InputNumber min={7} max={3650} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="auto_delete_after_days" label="Auto-Delete After (days)"
              dependencies={['stale_threshold_days']}
              tooltip="Non-prod tables that have completed GREENZONE review and are still inactive are dropped after this many days. Must exceed Stale Threshold -- it's the next stage of the same lifecycle."
              rules={[
                { required: true, type: 'number', min: 30, max: 3650, message: 'Enter a whole number between 30 and 3650' },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    const staleThreshold = getFieldValue('stale_threshold_days');
                    if (value == null || staleThreshold == null || value > staleThreshold) return Promise.resolve();
                    return Promise.reject(
                      new Error(`Must be greater than Stale Threshold (${staleThreshold} days) -- it's the later stage of the same non-prod lifecycle.`),
                    );
                  },
                }),
              ]}
            >
              <InputNumber min={30} max={3650} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item style={{ marginBottom: 16 }}>
          <Space size="large">
            <Form.Item name="archive_enabled" valuePropName="checked" noStyle>
              <Checkbox>Enable Archival</Checkbox>
            </Form.Item>
            <Form.Item name="is_active" valuePropName="checked" noStyle>
              <Checkbox>Domain Active</Checkbox>
            </Form.Item>
            {isEdit && (
              <Form.Item name="digest_enabled" valuePropName="checked" noStyle>
                <Checkbox>Weekly Digest Enabled</Checkbox>
              </Form.Item>
            )}
          </Space>
        </Form.Item>

        {isEdit && (
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                name="digest_email" label="Digest Email Override"
                rules={[{ type: 'email', message: 'Enter a valid email address' }]}
              >
                <Input placeholder="Leave blank to use Owner Email" />
              </Form.Item>
            </Col>
          </Row>
        )}

        <Form.Item name="notes" label="Notes">
          <Input.TextArea rows={2} />
        </Form.Item>
      </Form>

      {isEdit && domain && (
        <>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>Escalation / Notification Routing</div>
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="Owner Email">{domain.owner_email || '—'}</Descriptions.Item>
            <Descriptions.Item label="Digest Recipient">{domain.digest_email || domain.owner_email || '—'}</Descriptions.Item>
            <Descriptions.Item label="Weekly Digest">{domain.digest_enabled ? 'Enabled' : 'Disabled'}</Descriptions.Item>
          </Descriptions>
        </>
      )}
    </Modal>
  );
}
