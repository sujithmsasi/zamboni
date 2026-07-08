import { Alert, Button, Checkbox, Col, Divider, message, Row, Skeleton } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useUpdateSettings } from '../../../api/hooks/useSettings';
import { useSettingsPageData } from '../hooks';

export function EnforcementTab() {
  const { settings } = useSettingsPageData();
  const updateSettings = useUpdateSettings();

  const [reasonPreprod, setReasonPreprod] = useState(true);
  const [reasonDev, setReasonDev] = useState(false);
  const [ticketProd, setTicketProd] = useState(false);
  const [approval, setApproval] = useState<Record<string, boolean>>({});

  const applied = useRef(false);
  useEffect(() => {
    if (!settings.data || applied.current) return;
    applied.current = true;
    setReasonPreprod(!!settings.data.require_reason_in_preprod);
    setReasonDev(!!settings.data.require_reason_in_dev);
    setTicketProd(!!settings.data.require_ticket_in_prod);
    setApproval({ drop_table: false, bulk_hk_enable: false, promote_to_live: false, ...settings.data.approval_required_for });
  }, [settings.data]);

  const handleSave = () => {
    updateSettings.mutate(
      {
        settings: {
          require_reason_in_preprod: reasonPreprod,
          require_reason_in_dev: reasonDev,
          require_ticket_in_prod: ticketProd,
          approval_required_for: approval,
        },
        dry_run: false,
      },
      {
        onSuccess: (r) => message.success(`✅ Enforcement settings saved (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  if (settings.isLoading) return <Skeleton active />;

  return (
    <div style={{ maxWidth: 640 }}>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Reason / Ticket Requirements"
        description="Control when users must provide a reason and/or a change ticket before executing live or destructive actions."
      />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Checkbox checked={reasonPreprod} onChange={(e) => setReasonPreprod(e.target.checked)}>Require reason in preprod</Checkbox>
        </Col>
        <Col span={12}>
          <Checkbox checked={reasonDev} onChange={(e) => setReasonDev(e.target.checked)}>Require reason in dev/test</Checkbox>
        </Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 8 }}>
        <Col span={24}>
          <Checkbox checked={ticketProd} onChange={(e) => setTicketProd(e.target.checked)}>Require change ticket in prod</Checkbox>
        </Col>
      </Row>

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Approval Gates for Destructive Operations</div>
      <div style={{ fontSize: 12, color: '#667085', marginBottom: 12 }}>
        When enabled, these actions require a second user to approve before executing.
      </div>
      <Row gutter={[16, 8]} style={{ marginBottom: 24 }}>
        <Col span={24}>
          <Checkbox checked={!!approval.drop_table} onChange={(e) => setApproval((p) => ({ ...p, drop_table: e.target.checked }))}>
            Approve before dropping non-prod table
          </Checkbox>
        </Col>
        <Col span={24}>
          <Checkbox checked={!!approval.bulk_hk_enable} onChange={(e) => setApproval((p) => ({ ...p, bulk_hk_enable: e.target.checked }))}>
            Approve before bulk HK enable (&gt; 20 tables)
          </Checkbox>
        </Col>
        <Col span={24}>
          <Checkbox checked={!!approval.promote_to_live} onChange={(e) => setApproval((p) => ({ ...p, promote_to_live: e.target.checked }))}>
            Approve before dry-run promote to live
          </Checkbox>
        </Col>
      </Row>

      <Button type="primary" loading={updateSettings.isPending} onClick={handleSave}>
        💾 Save Enforcement Settings
      </Button>
    </div>
  );
}
