import { Alert, Col, DatePicker, Input, Row, Space, Switch } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { GovernanceChip } from './GovernanceChip';

export interface GatesValue {
  gate1_enabled: boolean;
  gate2_enabled: boolean;
  gate3_enabled: boolean;
  gate0_override_until?: string | null;
  gate0_override_reason?: string | null;
}

interface GatesEditorProps {
  value: GatesValue;
  onChange: (next: GatesValue) => void;
  /** Templates carry no per-table Gate 0 override state -- hide that section there. */
  showOverride?: boolean;
  /** contracts.md §6: override capped at GATE0_OVERRIDE_MAX_HOURS, from GET /api/system/mode. */
  overrideMaxHours?: number;
}

/**
 * Gate 1/2/3 toggles shared by Policy Configuration's Edit Table tab and
 * Templates editor (3_Policy_Configuration.py's gate toggle block, identical
 * in both places), plus the Gate 0 override control -- a wholly new feature
 * with no Streamlit twin (Workstream A's Gate 0 postdates the Streamlit
 * pages), only shown when showOverride is true.
 */
export function GatesEditor({ value, onChange, showOverride = true, overrideMaxHours = 24 }: GatesEditorProps) {
  const warnings: string[] = [];
  if (!value.gate1_enabled) warnings.push('Gate 1 disabled (no Control-M check)');
  if (!value.gate2_enabled) warnings.push('Gate 2 disabled (no blackout window)');
  if (!value.gate3_enabled) warnings.push('Gate 3 disabled (no circuit breaker)');

  const maxDate = dayjs().add(overrideMaxHours, 'hour');

  return (
    <div>
      <Row gutter={16}>
        <Col span={6}>
          <Space direction="vertical" size={0}>
            <span style={{ fontSize: 12, color: '#667085' }}>Gate 1 — Control-M upstream check</span>
            <Switch checked={value.gate1_enabled} onChange={(v) => onChange({ ...value, gate1_enabled: v })} />
          </Space>
        </Col>
        <Col span={6}>
          <Space direction="vertical" size={0}>
            <span style={{ fontSize: 12, color: '#667085' }}>Gate 2 — Blackout window</span>
            <Switch checked={value.gate2_enabled} onChange={(v) => onChange({ ...value, gate2_enabled: v })} />
          </Space>
        </Col>
        <Col span={6}>
          <Space direction="vertical" size={0}>
            <span style={{ fontSize: 12, color: '#667085' }}>Gate 3 — Circuit breaker</span>
            <Switch checked={value.gate3_enabled} onChange={(v) => onChange({ ...value, gate3_enabled: v })} />
          </Space>
        </Col>
        <Col span={6}>
          {warnings.length > 0 ? (
            <GovernanceChip text={warnings.join(' · ')} tone="warning" />
          ) : (
            <GovernanceChip text="All gates active" tone="success" />
          )}
        </Col>
      </Row>

      {showOverride && (
        <>
          <div style={{ marginTop: 16, marginBottom: 8, fontWeight: 600, fontSize: 13 }}>Gate 0 — Maintenance Override</div>
          {value.gate0_override_until && dayjs(value.gate0_override_until).isAfter(dayjs()) && (
            <Alert
              style={{ marginBottom: 8 }}
              type="warning"
              showIcon
              message={`Active override until ${dayjs(value.gate0_override_until).format('YYYY-MM-DD HH:mm')} — ${value.gate0_override_reason || 'no reason given'}`}
            />
          )}
          <Row gutter={16}>
            <Col span={10}>
              <DatePicker
                showTime
                style={{ width: '100%' }}
                placeholder={`Override until (max +${overrideMaxHours}h)`}
                value={value.gate0_override_until ? dayjs(value.gate0_override_until) : null}
                disabledDate={(d: Dayjs) => d.isBefore(dayjs(), 'day') || d.isAfter(maxDate, 'day')}
                onChange={(d) => onChange({ ...value, gate0_override_until: d ? d.toISOString() : null })}
              />
            </Col>
            <Col span={14}>
              <Input
                placeholder="Reason (required to set an override)"
                value={value.gate0_override_reason ?? ''}
                onChange={(e) => onChange({ ...value, gate0_override_reason: e.target.value })}
              />
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}
