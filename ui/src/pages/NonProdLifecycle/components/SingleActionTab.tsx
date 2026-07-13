import { Alert, Button, Input, message, Select } from 'antd';
import { useState } from 'react';
import { useClaimTables, useExemptTables, useNonprodList } from '../../../api/hooks/useLifecycle';
import { StateBadge } from '../../../components/StateBadge';

// Kept in sync with BulkActionTab.tsx's identical constant -- see the
// comment there.
const MIN_REASON_LENGTH = 10;

/**
 * Single Table Action tab. GET /api/nonprod has no search param, so this
 * fetches the full non-DROPPED list across every environment (capped at
 * 250) and filters client-side, not a server-side search.
 */
export function SingleActionTab() {
  const [fqn, setFqn] = useState<string | null>(null);
  const [reason, setReason] = useState('');

  const list = useNonprodList(undefined, 1, 250);
  const rows = list.data?.data ?? [];
  const selected = rows.find((r) => r.table_fqn === fqn);

  const exempt = useExemptTables();
  const claim = useClaimTables();

  const runAction = (kind: 'exempt' | 'claim') => {
    if (!fqn) {
      message.error('Select a table first.');
      return;
    }
    if (!reason.trim() || reason.trim().length < MIN_REASON_LENGTH) {
      message.error(`Reason must be at least ${MIN_REASON_LENGTH} characters.`);
      return;
    }
    const mutation = kind === 'exempt' ? exempt : claim;
    mutation.mutate(
      { fqns: [fqn], reason: reason.trim(), dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ Table \`${fqn}\` ${kind === 'exempt' ? 'exempted' : 'claimed'} and reset to ACTIVE (audit: ${r.audit_id}).`);
          setFqn(null);
          setReason('');
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Action failed.'),
      },
    );
  };

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="For individual tables — search by name, review its current state, then exempt or claim. Use the Bulk tab for multiple tables."
      />

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Search table</div>
        <Select
          showSearch
          style={{ width: 480 }}
          placeholder="Type to search non-prod tables"
          value={fqn}
          onChange={setFqn}
          loading={list.isLoading}
          optionFilterProp="label"
          options={rows.map((r) => ({
            value: r.table_fqn,
            label: `${r.table_fqn} · ${r.environment} · ${r.lifecycle_state} · ${r.days_since_activity ?? '—'}d inactive`,
          }))}
        />
      </div>

      {selected && (
        <div style={{ marginBottom: 16, fontSize: 13 }}>
          Environment: <strong>{selected.environment}</strong> · State:{' '}
          <StateBadge state={selected.lifecycle_state} /> · Days inactive:{' '}
          <strong>{selected.days_since_activity ?? '—'}</strong>
        </div>
      )}

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>Reason * (min {MIN_REASON_LENGTH} characters)</div>
        <Input.TextArea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why are you exempting or claiming ownership of this table?"
          rows={2}
          maxLength={500}
          showCount
        />
      </div>

      <Button type="primary" disabled={!fqn} loading={exempt.isPending} onClick={() => runAction('exempt')} style={{ marginRight: 12 }}>
        🛡️ Exempt This Table
      </Button>
      <Button disabled={!fqn} loading={claim.isPending} onClick={() => runAction('claim')}>
        🙋 Claim This Table
      </Button>
    </div>
  );
}
