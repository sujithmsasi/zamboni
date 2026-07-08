import { Alert, Button, Card, Col, Divider, Input, message, Row, Skeleton, Switch } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useLocks } from '../../../api/hooks/useSystem';
import { useUpdateSettings } from '../../../api/hooks/useSettings';
import { LocksStrip } from '../../LiveActivity/components/LocksStrip';
import { useSettingsPageData } from '../hooks';

/**
 * Advanced tab (11_Settings.py tab_advanced). "Send Test Message" is
 * deliberately not ported -- it's a live notification-send action, not a
 * settings field, and has no contract endpoint (same class of decision as
 * DryRunViewer's "Promote to Live" in Wave 1). SSO/LDAP stays a static
 * caption, matching the twin -- it has no real control there either.
 *
 * Active Maintenance Locks: reuses LiveActivity's LocksStrip directly
 * rather than duplicating it or just linking out -- it's a decoupled
 * component (takes a queryResult prop), and Settings/Administration is a
 * natural home for an admin force-release action.
 */
export function AdvancedTab() {
  const { settings } = useSettingsPageData();
  const updateSettings = useUpdateSettings();
  const locks = useLocks();

  const [patterns, setPatterns] = useState('');
  const [teamsEnabled, setTeamsEnabled] = useState(false);
  const [teamsUrl, setTeamsUrl] = useState('');
  const [ceEnabled, setCeEnabled] = useState(false);
  const [tagKey, setTagKey] = useState('zamboni:managed');
  const [tagValue, setTagValue] = useState('true');

  const applied = useRef(false);
  useEffect(() => {
    if (!settings.data || applied.current) return;
    applied.current = true;
    setPatterns((settings.data.backup_stale_name_patterns ?? []).join('\n'));
    setTeamsEnabled(!!settings.data.teams_enabled);
    setCeEnabled(!!settings.data.cost_explorer_enabled);
    setTagKey(settings.data.cost_explorer_tag_key ?? 'zamboni:managed');
    setTagValue(settings.data.cost_explorer_tag_value ?? 'true');
  }, [settings.data]);

  const savePatterns = () => {
    const list = patterns.split('\n').map((p) => p.trim()).filter(Boolean);
    updateSettings.mutate(
      { settings: { backup_stale_name_patterns: list }, dry_run: false },
      {
        onSuccess: (r) => message.success(`✅ Patterns saved (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  const saveTeams = () => {
    const trimmedUrl = teamsUrl.trim();
    if (trimmedUrl && !/^https?:\/\/.+/i.test(trimmedUrl)) {
      message.error('Webhook URL must start with http:// or https://');
      return;
    }
    if (teamsEnabled && !trimmedUrl && !settings.data?.teams_webhook_url) {
      message.error('Enter a webhook URL before enabling Teams notifications.');
      return;
    }
    const body: Record<string, unknown> = { teams_enabled: teamsEnabled };
    // Leave blank to keep the existing URL -- the value we read back is
    // masked (settings_svc.get_settings), so re-submitting it unmodified
    // would overwrite the real webhook with masked garbage.
    if (trimmedUrl) body.teams_webhook_url = trimmedUrl;
    updateSettings.mutate(
      { settings: body, dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ Teams settings saved (audit: ${r.audit_id}).`);
          setTeamsUrl('');
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  const saveCostExplorer = () => {
    if (ceEnabled && (!tagKey.trim() || !tagValue.trim())) {
      message.error('Cost allocation tag key and value are required when Cost Explorer is enabled.');
      return;
    }
    updateSettings.mutate(
      { settings: { cost_explorer_enabled: ceEnabled, cost_explorer_tag_key: tagKey.trim(), cost_explorer_tag_value: tagValue.trim() }, dry_run: false },
      {
        onSuccess: (r) => message.success(`✅ Cost Explorer settings saved (audit: ${r.audit_id}).`),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Save failed.'),
      },
    );
  };

  if (settings.isLoading) return <Skeleton active />;

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Backup / Stale Table Naming Patterns</div>
      <div style={{ fontSize: 12, color: '#667085', marginBottom: 8 }}>
        Tables matching these patterns are auto-flagged as backup candidates by the Lifecycle Engine.
      </div>
      <Input.TextArea value={patterns} onChange={(e) => setPatterns(e.target.value)} rows={5} placeholder="_bkp" style={{ marginBottom: 8 }} />
      <Button onClick={savePatterns} loading={updateSettings.isPending} style={{ marginBottom: 24 }}>💾 Save Patterns</Button>

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Microsoft Teams Notifications</div>
      <div style={{ fontSize: 12, color: '#667085', marginBottom: 8 }}>
        Fires Teams messages for key user actions. Does NOT fire on dry-run actions.
      </div>
      <div style={{ marginBottom: 8 }}>
        <Switch checked={teamsEnabled} onChange={setTeamsEnabled} /> <span style={{ marginLeft: 8 }}>Enable Teams notifications</span>
      </div>
      <Row gutter={16} align="middle" style={{ marginBottom: 24 }}>
        <Col span={16}>
          <Input.Password
            value={teamsUrl}
            onChange={(e) => setTeamsUrl(e.target.value)}
            placeholder={settings.data?.teams_webhook_url || 'https://outlook.office.com/webhook/...'}
          />
          <div style={{ fontSize: 11, color: '#667085', marginTop: 4 }}>Leave blank to keep the existing URL.</div>
        </Col>
        <Col span={8}>
          <Button onClick={saveTeams} loading={updateSettings.isPending}>💾 Save Teams Settings</Button>
        </Col>
      </Row>

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>AWS Cost Explorer Integration</div>
      <div style={{ fontSize: 12, color: '#667085', marginBottom: 8 }}>
        Pull real Athena billing data instead of estimating from bytes_scanned. Cached 24h.
      </div>
      <div style={{ marginBottom: 8 }}>
        <Switch checked={ceEnabled} onChange={setCeEnabled} /> <span style={{ marginLeft: 8 }}>Enable Cost Explorer integration</span>
      </div>
      {ceEnabled && (
        <Row gutter={16} style={{ marginBottom: 12 }}>
          <Col span={12}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Cost allocation tag key</div>
            <Input value={tagKey} onChange={(e) => setTagKey(e.target.value)} />
          </Col>
          <Col span={12}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Cost allocation tag value</div>
            <Input value={tagValue} onChange={(e) => setTagValue(e.target.value)} />
          </Col>
        </Row>
      )}
      <Button onClick={saveCostExplorer} loading={updateSettings.isPending} style={{ marginBottom: 24 }}>💾 Save Cost Explorer Settings</Button>

      <Divider />
      <Alert type="info" showIcon message="SSO / LDAP Integration (Phase 2)" description="Planned: replace username/password auth with SSO for automatic user population and LDAP-based role mapping. Not active." style={{ marginBottom: 24 }} />

      <Divider />
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Active Maintenance Locks</div>
      <Card size="small">
        <LocksStrip queryResult={locks} />
      </Card>
    </div>
  );
}
