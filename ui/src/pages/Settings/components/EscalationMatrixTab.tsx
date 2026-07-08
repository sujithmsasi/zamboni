import { Plus } from '@phosphor-icons/react';
import { Alert, Button, Drawer, Form, Input, message, Modal, Table } from 'antd';
import { useState } from 'react';
import { useCreateEscalation, useDeleteEscalation, useEscalationList, useUpdateEscalation } from '../../../api/hooks/useSettings';
import type { EscalationEntry } from '../../../api/types';

/**
 * Escalation Matrix tab (11_Settings.py tab_escalation). Unlike the twin --
 * which needed a `st.session_state["esc_flash"]` + st.rerun() dance to show
 * a success message across a full-page rerun -- TanStack Query's mutation
 * onSuccess + query invalidation gives instant grid refresh for free, so
 * the old navigate-away-and-lose-the-flash class of bug is structurally
 * gone here, not just fixed.
 */
export function EscalationMatrixTab() {
  const list = useEscalationList();
  const rows = list.data ?? [];

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<EscalationEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<EscalationEntry | null>(null);
  const [form] = Form.useForm();

  const create = useCreateEscalation();
  const update = useUpdateEscalation();
  const del = useDeleteEscalation();

  const openAdd = () => {
    setEditing(null);
    form.resetFields();
    setDrawerOpen(true);
  };

  const openEdit = (row: EscalationEntry) => {
    setEditing(row);
    form.setFieldsValue(row);
    setDrawerOpen(true);
  };

  const handleSubmit = async () => {
    const values = await form.validateFields();
    const key = editing ? editing._key : values._key.trim();
    const entry = {
      primary_owner_email: values.primary_owner_email?.trim() ?? '',
      escalation_email: values.escalation_email?.trim() ?? '',
      zamboni_owner_email: values.zamboni_owner_email?.trim() ?? '',
      notify_sns_topic: values.notify_sns_topic?.trim() ?? '',
    };
    const mutation = editing ? update : create;
    mutation.mutate(
      { key, entry, dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ Entry '${key}' saved (audit: ${r.audit_id}).`);
          setDrawerOpen(false);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    del.mutate(
      { key: deleteTarget._key, dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ Entry '${deleteTarget._key}' deleted (audit: ${r.audit_id}).`);
          setDeleteTarget(null);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Delete failed.'),
      },
    );
  };

  const columns = [
    { title: 'Lookup Key', dataIndex: '_key', key: '_key' },
    { title: 'Primary Owner', dataIndex: 'primary_owner_email', key: 'primary_owner_email' },
    { title: 'Escalation Email', dataIndex: 'escalation_email', key: 'escalation_email' },
    { title: 'Zamboni Owner', dataIndex: 'zamboni_owner_email', key: 'zamboni_owner_email' },
    { title: 'SNS Topic Override', dataIndex: 'notify_sns_topic', key: 'notify_sns_topic', ellipsis: true },
    {
      title: '', key: 'actions', width: 140,
      render: (_: unknown, row: EscalationEntry) => (
        <>
          <Button size="small" onClick={() => openEdit(row)} style={{ marginRight: 8 }}>Edit</Button>
          <Button size="small" danger onClick={() => setDeleteTarget(row)}>Delete</Button>
        </>
      ),
    },
  ];

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Route alerts and notifications by domain, tier, and environment."
        description="Lookup is most-specific-first: domain+tier+env → domain+env → domain → tier+env → env → default."
      />

      <Button type="primary" icon={<Plus size={14} />} onClick={openAdd} style={{ marginBottom: 16 }}>
        Add Entry
      </Button>

      <Table<EscalationEntry>
        size="small"
        loading={list.isLoading}
        columns={columns}
        dataSource={rows}
        rowKey="_key"
        pagination={rows.length > 15 ? { pageSize: 15 } : false}
      />

      <Drawer
        title={editing ? `✏️ Editing: ${editing._key}` : '➕ Add New Entry'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={420}
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={handleSubmit}>
            {editing ? 'Update Entry' : 'Add Entry'}
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="_key" label="Lookup Key"
            rules={[{ required: true, message: 'Lookup Key is required' }]}
            help="Examples: domain:finance|tier:critical|env:prod, domain:finance, tier:critical|env:prod, default"
          >
            <Input disabled={!!editing} placeholder="domain:finance|env:prod" />
          </Form.Item>
          <Form.Item name="primary_owner_email" label="Primary Owner Email">
            <Input placeholder="team-dl@company.com" />
          </Form.Item>
          <Form.Item name="escalation_email" label="Escalation Email">
            <Input placeholder="manager-dl@company.com" />
          </Form.Item>
          <Form.Item name="zamboni_owner_email" label="Zamboni Owner Email">
            <Input placeholder="da-platform@company.com" />
          </Form.Item>
          <Form.Item name="notify_sns_topic" label="SNS Topic ARN Override">
            <Input placeholder="arn:aws:sns:us-west-2:123:my-topic (blank = default)" />
          </Form.Item>
        </Form>
      </Drawer>

      <Modal
        open={!!deleteTarget}
        title="Delete escalation entry"
        okText="Delete" okButtonProps={{ danger: true, loading: del.isPending }}
        onCancel={() => setDeleteTarget(null)}
        onOk={handleDelete}
      >
        Delete entry <strong>{deleteTarget?._key}</strong>? This cannot be undone.
      </Modal>
    </div>
  );
}
