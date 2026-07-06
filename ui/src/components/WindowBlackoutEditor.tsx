import { Checkbox, Col, Input, InputNumber, Row, Select } from 'antd';
import { useState } from 'react';
import type { WindowConfig } from '../api/types';

const TIMEZONE_OPTIONS = ['America/Los_Angeles', 'America/New_York', 'America/Chicago', 'UTC'].map((tz) => ({
  value: tz,
  label: tz,
}));

const BLACKOUT_PRESETS: Record<string, number[] | null> = {
  '— manual —': null,
  'Business hours (6–18)': Array.from({ length: 13 }, (_, i) => i + 6),
  'Midnight window (22–5)': [22, 23, 0, 1, 2, 3, 4, 5],
  'Peak hours (7–9, 17–20)': [7, 8, 9, 17, 18, 19, 20],
  'Weekday peak (7–20)': Array.from({ length: 14 }, (_, i) => i + 7),
  'None (always allowed)': [],
  'Always blocked': Array.from({ length: 24 }, (_, i) => i),
};

interface WindowBlackoutEditorProps {
  value: WindowConfig;
  onChange: (next: WindowConfig) => void;
}

/**
 * Window type/timing + 24-hour blackout grid, shared by Policy Configuration's
 * Edit Table tab and Templates editor (3_Policy_Configuration.py's window
 * editor block, identical fields in both places). Reacts instantly to every
 * change -- no Streamlit rerun/session-state gymnastics needed in React.
 */
export function WindowBlackoutEditor({ value, onChange }: WindowBlackoutEditorProps) {
  const [preset, setPreset] = useState('— manual —');

  const handlePreset = (label: string) => {
    setPreset(label);
    const hours = BLACKOUT_PRESETS[label];
    if (hours !== null) {
      onChange({ ...value, blackout_hours: hours });
    }
  };

  const toggleHour = (h: number, checked: boolean) => {
    const next = checked
      ? [...value.blackout_hours, h].sort((a, b) => a - b)
      : value.blackout_hours.filter((x) => x !== h);
    onChange({ ...value, blackout_hours: next });
  };

  return (
    <Row gutter={24}>
      <Col span={12}>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Window Type</div>
          <Select
            style={{ width: '100%' }}
            value={value.type}
            onChange={(type) => onChange({ ...value, type })}
            options={[
              { value: 'post_batch', label: 'post_batch — Gate 1 + delay' },
              { value: 'scheduled', label: 'scheduled — fixed daily time' },
            ]}
          />
        </div>
        {value.type === 'scheduled' && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Start time (HH:MM)</div>
            <Input
              value={value.start_time}
              placeholder="02:00"
              onChange={(e) => onChange({ ...value, start_time: e.target.value })}
            />
          </div>
        )}
        <Row gutter={12}>
          <Col span={12}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Delay after job (min)</div>
            <InputNumber
              min={0} max={240} style={{ width: '100%' }}
              disabled={value.type === 'scheduled'}
              value={value.delay_minutes}
              onChange={(v) => onChange({ ...value, delay_minutes: v ?? 0 })}
            />
          </Col>
          <Col span={12}>
            <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Duration (hours)</div>
            <InputNumber
              min={1} max={12} style={{ width: '100%' }}
              value={value.duration_hours}
              onChange={(v) => onChange({ ...value, duration_hours: v ?? 1 })}
            />
          </Col>
        </Row>
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Timezone</div>
          <Select
            style={{ width: '100%' }}
            value={value.timezone}
            onChange={(timezone) => onChange({ ...value, timezone })}
            options={TIMEZONE_OPTIONS}
          />
        </div>
      </Col>
      <Col span={12}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Blackout preset</div>
        <Select
          style={{ width: '100%', marginBottom: 8 }}
          value={preset}
          onChange={handlePreset}
          options={Object.keys(BLACKOUT_PRESETS).map((label) => ({ value: label, label }))}
        />
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 6 }}>HK will not start during checked hours</div>
        <Row gutter={[8, 4]}>
          {Array.from({ length: 24 }, (_, h) => (
            <Col span={6} key={h}>
              <Checkbox checked={value.blackout_hours.includes(h)} onChange={(e) => toggleHour(h, e.target.checked)}>
                {String(h).padStart(2, '0')}:00
              </Checkbox>
            </Col>
          ))}
        </Row>
      </Col>
    </Row>
  );
}
