import { Alert, Col, Row } from 'antd';
import type { AuditRow } from '../../../api/types';

const CODE_BLOCK: React.CSSProperties = {
  background: '#F7F9FC', padding: 8, borderRadius: 6, fontSize: 12,
  whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
};

export function AuditDetailPanel({ row }: { row: AuditRow }) {
  return (
    <div style={{ padding: '4px 12px 12px' }}>
      <Row gutter={16}>
        <Col span={12}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Before</div>
          <pre style={CODE_BLOCK}>{row.before_value || '(none)'}</pre>
        </Col>
        <Col span={12}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>After</div>
          <pre style={CODE_BLOCK}>{row.after_value || '(none)'}</pre>
        </Col>
      </Row>
      {row.error_message ? <Alert style={{ marginTop: 8 }} type="error" showIcon message={row.error_message} /> : null}
    </div>
  );
}
