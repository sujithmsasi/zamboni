import { Alert, Button, Card, Checkbox, Col, Divider, Form, Input, message, Row, Select, Skeleton } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useTableDetail, useTablesSearch, useUpdateTable } from '../../../api/hooks/useTables';
import { ControlMFields } from '../../../components/ControlMFields';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];
const VALID_TIERS = ['critical', 'standard', 'low'];
const CADENCE_OPTIONS = ['daily', 'weekly', 'monthly', 'hourly', 'every_trigger'];

/** Edit Table tab (2_Table_Registration.py tab_edit_reg) -- search, edit, single PUT. */
export function EditTableTab() {
  const [fqn, setFqn] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [form] = Form.useForm();

  const domains = useDomainsList(true);
  // Search-as-you-type (contracts.md §6 GET /api/tables?search=) rather than
  // loading the whole fleet into one dropdown -- size is capped at 250 by
  // api/deps.py::PageParams, and a real fleet runs ~30k tables anyway.
  const tables = useTablesSearch(search);
  const detail = useTableDetail(fqn);
  const update = useUpdateTable();

  // Keyed on fqn (not detail.data's identity) so a background refetch of the
  // same table mid-edit doesn't silently overwrite unsaved form changes --
  // see the identical note in PolicyConfig/components/EditTableTab.tsx.
  const appliedForFqn = useRef<string | null>(null);
  useEffect(() => {
    if (!detail.data || appliedForFqn.current === fqn) return;
    appliedForFqn.current = fqn;
    form.setFieldsValue(detail.data);
  }, [detail.data, fqn, form]);

  const handleSave = async () => {
    if (!fqn) return;
    const values = await form.validateFields();
    update.mutate(
      { fqn, body: { ...values, dry_run: false } },
      {
        onSuccess: (r) => message.success(`✅ ${fqn} updated (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Update failed.'),
      },
    );
  };

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Select table to edit</div>
        <Select
          showSearch
          style={{ width: 420 }}
          placeholder="Type table name or database to search"
          value={fqn}
          onChange={setFqn}
          onSearch={setSearch}
          filterOption={false}
          options={(tables.data?.data ?? []).map((t) => ({ value: t.table_fqn, label: t.table_fqn }))}
          notFoundContent={tables.isLoading ? 'Searching…' : 'Type to search'}
        />
      </div>

      {!fqn && <Alert type="info" showIcon message="Select a table above to edit its metadata." />}

      {fqn && detail.isLoading && <Skeleton active />}

      {fqn && detail.data && (
        <Card size="small" title={`Editing: ${fqn}`}>
          <Form form={form} layout="vertical">
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item name="domain" label="Domain">
                  <Select options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="layer" label="Layer">
                  <Select options={VALID_LAYERS.map((l) => ({ value: l, label: l }))} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="tier" label="Tier">
                  <Select options={VALID_TIERS.map((t) => ({ value: t, label: t }))} />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item name="owner_email" label="Owner Email"><Input /></Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="ci_number" label="CI Number"><Input /></Form.Item>
              </Col>
            </Row>
            <Divider orientation="left" style={{ margin: '8px 0 16px' }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>⚙️ Engine Flags</span>
            </Divider>
            <Row gutter={16}>
              <Col span={6}>
                <Form.Item name="hk_enabled" valuePropName="checked" label=" ">
                  <Checkbox>🔧 Housekeeping</Checkbox>
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="archive_enabled" valuePropName="checked" label=" ">
                  <Checkbox>📦 Archival</Checkbox>
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="lifecycle_enabled" valuePropName="checked" label=" ">
                  <Checkbox>♻️ Lifecycle</Checkbox>
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="processing_cadence" label="Processing Cadence">
                  <Select options={CADENCE_OPTIONS.map((c) => ({ value: c, label: c }))} />
                </Form.Item>
              </Col>
            </Row>
            <Divider orientation="left" style={{ margin: '8px 0 16px' }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>🔗 Control-M Integration</span>
            </Divider>
            <ControlMFields jobNameRequired />
            <Button type="primary" onClick={handleSave} loading={update.isPending}>💾 Save Changes</Button>
          </Form>
        </Card>
      )}
    </div>
  );
}
