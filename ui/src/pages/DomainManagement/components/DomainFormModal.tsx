import { Checkbox, Descriptions, Form, Input, InputNumber, message, Modal } from 'antd';
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
      title={isEdit ? `Edit Domain — ${domain?.domain_name}` : 'Register New Domain'}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={create.isPending || update.isPending}
      width={640}
      okText={isEdit ? 'Update Domain' : 'Register Domain'}
    >
      <Form form={form} layout="vertical" initialValues={CREATE_DEFAULTS}>
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
        <Form.Item name="owner_email" label="Owner Email" rules={[{ required: true }]}>
          <Input placeholder="da-finance@company.com" />
        </Form.Item>
        <Form.Item name="team_name" label="Team">
          <Input placeholder="Data & Analytics - Finance" />
        </Form.Item>
        <Form.Item name="ci_number" label="CI Number">
          <Input placeholder="CI-10234" />
        </Form.Item>
        <Form.Item name="archive_enabled" label="Enable Archival" valuePropName="checked">
          <Checkbox />
        </Form.Item>
        <Form.Item name="hot_retention_days" label="Hot Retention (days)">
          <InputNumber min={1} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="archive_duration_days" label="Archive Duration (days)">
          <InputNumber min={1} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="stale_threshold_days" label="Stale Threshold (days)">
          <InputNumber min={7} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="auto_delete_after_days" label="Auto-Delete After (days)">
          <InputNumber min={30} style={{ width: '100%' }} />
        </Form.Item>
        {isEdit && (
          <>
            <Form.Item name="is_active" label="Domain Active" valuePropName="checked">
              <Checkbox />
            </Form.Item>
            <Form.Item name="digest_enabled" label="Weekly Digest Enabled" valuePropName="checked">
              <Checkbox />
            </Form.Item>
            <Form.Item name="digest_email" label="Digest Email Override">
              <Input placeholder="Leave blank to use Owner Email" />
            </Form.Item>
          </>
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
