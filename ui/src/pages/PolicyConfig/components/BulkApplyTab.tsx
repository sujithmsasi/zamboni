import { Alert, Button, Card, Checkbox, Col, Row, Select, Space, Statistic, message } from 'antd';
import { useEffect, useState } from 'react';
import { useDomainsList } from '../../../api/hooks/useDomains';
import { useApplyTemplate, useTemplates } from '../../../api/hooks/usePolicies';

const VALID_LAYERS = ['staging', 'datalake', 'base', 'master'];

/**
 * Bulk Apply Template tab (3_Policy_Configuration.py tab_bulk). The
 * "Override existing manual overrides" checkbox maps to skip_overridden on
 * POST /api/templates/{name}/apply -- unchecked (default) skips any table
 * with manually_overridden=1, exactly like the Streamlit twin.
 *
 * Preview-before-apply (added 2026-07-10): this mutates fleet-wide ("ALL
 * registered Iceberg tables in the chosen domain and layer") with only a
 * disabled-button guard and no affected-count preview -- ManualApply.tsx
 * and DeleteTemplate.tsx both already gate an equally wide action behind
 * a preview/confirm step, this tab didn't. No new endpoint needed: the
 * existing apply mutation already supports dry_run and returns the real
 * `affected` count either way (apply_template_bulk() counts matched
 * tables the same way regardless of dry_run) -- reused here as a preview
 * call instead of adding a second endpoint.
 */
export function BulkApplyTab() {
  const [domain, setDomain] = useState<string | undefined>();
  const [layer, setLayer] = useState<string | undefined>();
  const [template, setTemplate] = useState<string | undefined>();
  const [overrideManual, setOverrideManual] = useState(false);
  const [previewCount, setPreviewCount] = useState<number | null>(null);

  const domains = useDomainsList(true);
  const templates = useTemplates();
  const apply = useApplyTemplate();
  const preview = useApplyTemplate();

  const tpl = template ? templates.data?.[template] : undefined;
  const selectionIncomplete = !domain || !layer || !template;

  // Any change to the scope invalidates a previous preview -- applying a
  // stale count against a since-changed domain/layer/template/override
  // selection would confirm the wrong thing.
  useEffect(() => {
    setPreviewCount(null);
  }, [domain, layer, template, overrideManual]);

  const handlePreview = () => {
    if (!template) return;
    preview.mutate(
      { name: template, body: { domain, layer, skip_overridden: !overrideManual, dry_run: true } },
      {
        onSuccess: (r) => setPreviewCount(r.affected),
        onError: (err) => message.error(err instanceof Error ? err.message : 'Preview failed.'),
      },
    );
  };

  const handleApply = () => {
    if (!template) return;
    apply.mutate(
      { name: template, body: { domain, layer, skip_overridden: !overrideManual, dry_run: false } },
      {
        onSuccess: (r) => {
          message.success(`✅ Template ${template} applied to ${r.affected} table(s) (audit: ${r.audit_id}).`);
          setPreviewCount(null);
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Bulk apply failed.'),
      },
    );
  };

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Applies the selected template to ALL registered Iceberg tables in the chosen domain and layer."
      />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Domain *</div>
          <Select
            style={{ width: '100%' }} value={domain} onChange={setDomain}
            options={(domains.data ?? []).map((d) => ({ value: d.domain_name, label: d.domain_name }))}
          />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Layer *</div>
          <Select style={{ width: '100%' }} value={layer} onChange={setLayer} options={VALID_LAYERS.map((l) => ({ value: l, label: l }))} />
        </Col>
        <Col span={8}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Template</div>
          <Select
            style={{ width: '100%' }} value={template} onChange={setTemplate} loading={templates.isLoading}
            options={Object.keys(templates.data ?? {}).map((t) => ({ value: t, label: t }))}
          />
        </Col>
      </Row>

      {tpl && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}><Card size="small"><Statistic title="Strategy" value={`${tpl.compaction_strategy} / ${tpl.compaction_engine}`} /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="Retention" value={`${tpl.snapshot_retention_days}d`} /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="Orphan" value={`${tpl.orphan_file_retention_days}d`} /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="Frequency" value={tpl.run_frequency} /></Card></Col>
        </Row>
      )}

      <Checkbox checked={overrideManual} onChange={(e) => setOverrideManual(e.target.checked)} style={{ marginBottom: 16 }}>
        Override existing manual overrides
      </Checkbox>
      <br />

      {previewCount === null ? (
        <Space direction="vertical">
          <Button disabled={selectionIncomplete} loading={preview.isPending} onClick={handlePreview}>
            🔍 Preview Affected Tables
          </Button>
          {!selectionIncomplete && (
            <span style={{ fontSize: 12, color: '#667085' }}>Preview before applying -- this affects every matching table at once.</span>
          )}
        </Space>
      ) : (
        <Space direction="vertical">
          <Alert
            type={previewCount > 0 ? 'warning' : 'info'}
            showIcon
            message={
              previewCount > 0
                ? `${previewCount} table(s) will be updated.`
                : 'No tables match this domain/layer (with the current override setting).'
            }
          />
          <Space>
            <Button
              type="primary" danger={previewCount > 0} disabled={previewCount === 0}
              loading={apply.isPending} onClick={handleApply}
            >
              🔄 Confirm — Apply to {previewCount} Table(s)
            </Button>
            <Button onClick={() => setPreviewCount(null)}>Change selection</Button>
          </Space>
        </Space>
      )}
    </div>
  );
}
