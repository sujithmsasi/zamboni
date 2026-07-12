import { useQueries } from '@tanstack/react-query';
import { Alert, Button, Input, message, Table } from 'antd';
import { useState } from 'react';
import { qs, requestPaged } from '../../../api/client';
import { useClaimTables, useExemptTables } from '../../../api/hooks/useLifecycle';
import type { NonprodRow } from '../../../api/types';
import { StateBadge } from '../../../components/StateBadge';

// Kept in sync with SingleActionTab.tsx's identical constant -- both tabs
// validate the same "Business Reason" concept and must agree on the bar,
// which used to be inconsistent (this tab accepted any non-empty string,
// SingleActionTab required 10+ chars for the same action).
const MIN_REASON_LENGTH = 10;

const ACTIONABLE_STATES = ['PENDING_DROP', 'GREENZONE', 'STALE_CANDIDATE'] as const;
const STATE_PRIORITY: Record<string, number> = { PENDING_DROP: 1, GREENZONE: 2, STALE_CANDIDATE: 3 };

const columns = [
  { title: 'Table', dataIndex: 'table_fqn', key: 'table_fqn', ellipsis: true },
  { title: 'Domain', dataIndex: 'domain', key: 'domain', width: 120 },
  {
    title: 'State', dataIndex: 'lifecycle_state', key: 'lifecycle_state', width: 140,
    render: (v: string) => <StateBadge state={v} />,
  },
  { title: 'Days Inactive', dataIndex: 'days_since_activity', key: 'days_since_activity', width: 120 },
  { title: 'GZ Expires', dataIndex: 'greenzone_expires_at', key: 'greenzone_expires_at', width: 150 },
  { title: 'Drop At', dataIndex: 'pending_drop_expires_at', key: 'pending_drop_expires_at', width: 150 },
];

/**
 * Bulk Exemption / Claim tab (9_NonProd_Lifecycle.py tab2). GET /api/nonprod
 * only filters to one state at a time, but the twin's bulk grid shows all
 * three actionable states merged and priority-sorted (PENDING_DROP first) --
 * fetched here as 3 parallel queries and merged client-side, same pattern as
 * State Overview's per-state count queries. The merged/sorted array is a
 * fixed in-memory list, not a server-paginated resource, so this uses a
 * plain <Table> (client pagination) rather than <DataGrid> -- same
 * documented exception as CostReport/DomainManagement's bare-array grids.
 */
export function BulkActionTab({ env }: { env: string }) {
  const [selectedFqns, setSelectedFqns] = useState<string[]>([]);
  const [reason, setReason] = useState('');

  const results = useQueries({
    queries: ACTIONABLE_STATES.map((state) => ({
      queryKey: ['nonprod', 'bulk-candidates', env, state],
      queryFn: () => requestPaged<NonprodRow[]>(`/nonprod${qs({ env, state, page: 1, size: 250 })}`),
    })),
  });
  const isLoading = results.some((r) => r.isLoading);
  const rows = results
    .flatMap((r) => r.data?.data ?? [])
    .sort((a, b) => {
      const p = (STATE_PRIORITY[a.lifecycle_state] ?? 9) - (STATE_PRIORITY[b.lifecycle_state] ?? 9);
      if (p !== 0) return p;
      return (b.days_since_activity ?? 0) - (a.days_since_activity ?? 0);
    });

  const exempt = useExemptTables();
  const claim = useClaimTables();

  const runAction = (kind: 'exempt' | 'claim') => {
    if (!reason.trim() || reason.trim().length < MIN_REASON_LENGTH) {
      message.error(`Business reason must be at least ${MIN_REASON_LENGTH} characters.`);
      return;
    }
    const mutation = kind === 'exempt' ? exempt : claim;
    mutation.mutate(
      { fqns: selectedFqns, reason: reason.trim(), dry_run: false },
      {
        onSuccess: (r) => {
          message.success(`✅ ${kind === 'exempt' ? 'Exempted' : 'Claimed'} ${r.affected} table(s) (audit: ${r.audit_id}).`);
          setSelectedFqns([]);
          setReason('');
        },
        onError: (err) => message.error(err instanceof Error ? err.message : 'Action failed.'),
      },
    );
  };

  if (!isLoading && rows.length === 0) {
    return <Alert type="success" showIcon message="No tables currently require exemption or claiming in this environment." />;
  }

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="info" showIcon
        message="Select tables below. Exemption resets to ACTIVE for one more cycle. Claim assigns you as owner and resets to ACTIVE. Both require a business reason."
      />
      <Table<NonprodRow>
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        rowKey="table_fqn"
        rowSelection={{ selectedRowKeys: selectedFqns, onChange: (keys) => setSelectedFqns(keys as string[]) }}
        pagination={rows.length > 15 ? { pageSize: 15 } : false}
        style={{ marginBottom: 16 }}
      />

      {selectedFqns.length > 0 && <div style={{ marginBottom: 8, fontWeight: 600 }}>{selectedFqns.length} table(s) selected</div>}

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#667085', marginBottom: 4 }}>
          Business Reason (required for all selected tables, min {MIN_REASON_LENGTH} characters)
        </div>
        <Input.TextArea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Used by Q2 reporting sprint — will be cleaned up by 2026-06-30"
          rows={2}
          maxLength={500}
          showCount
        />
      </div>

      <Button type="primary" disabled={selectedFqns.length === 0} loading={exempt.isPending} onClick={() => runAction('exempt')} style={{ marginRight: 12 }}>
        🛡️ Exempt {selectedFqns.length} Table(s)
      </Button>
      <Button disabled={selectedFqns.length === 0} loading={claim.isPending} onClick={() => runAction('claim')}>
        🙋 Claim {selectedFqns.length} Table(s)
      </Button>
    </div>
  );
}
