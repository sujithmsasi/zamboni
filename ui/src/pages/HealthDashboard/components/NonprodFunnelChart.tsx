import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { statusTagStyles } from '../../../colors';
import type { NonprodFunnelPoint } from '../../../api/types';

interface NonprodFunnelChartProps {
  data: NonprodFunnelPoint[];
}

const FUNNEL_ORDER = ['ACTIVE', 'STALE_CANDIDATE', 'GREENZONE', 'PENDING_DROP', 'DROPPED'];

/** Non-prod cleanup funnel, in lifecycle order — reuses the same state colors as StateBadge. */
export function NonprodFunnelChart({ data }: NonprodFunnelChartProps) {
  const byState = new Map(data.map((d) => [d.lifecycle_state, d.count]));
  const rows = FUNNEL_ORDER.filter((s) => byState.has(s)).map((s) => ({
    lifecycle_state: s,
    count: byState.get(s) ?? 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={Math.max(160, rows.length * 40)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: '#667085' }} />
        <YAxis type="category" dataKey="lifecycle_state" width={120} tick={{ fontSize: 11, fill: '#344054' }} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }} />
        <Bar dataKey="count" radius={[0, 6, 6, 0]} barSize={22}>
          {rows.map((r) => (
            <Cell key={r.lifecycle_state} fill={(statusTagStyles[r.lifecycle_state] ?? statusTagStyles.DROPPED).text} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
