import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { TopReclaimTable } from '../../../api/types';

interface TopReclaimChartProps {
  data: TopReclaimTable[];
}

function shortName(fqn: string): string {
  const parts = fqn.split('.');
  return parts[parts.length - 1] ?? fqn;
}

/** Leaderboard of the tables that gave back the most storage — the "who benefited most" VP view. */
export function TopReclaimChart({ data }: TopReclaimChartProps) {
  const rows = data.map((r) => ({ ...r, short: shortName(r.table_fqn) }));

  return (
    <ResponsiveContainer width="100%" height={Math.max(180, rows.length * 32)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
        <XAxis type="number" tickFormatter={(v) => `${v} GB`} tick={{ fontSize: 11, fill: '#667085' }} />
        <YAxis type="category" dataKey="short" width={160} tick={{ fontSize: 11, fill: '#344054' }} />
        <Tooltip
          formatter={(value, _name, item) => [`${value} GB`, (item.payload as TopReclaimTable).domain]}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }}
        />
        <Bar dataKey="gb_reclaimed" fill="#167D9A" radius={[0, 6, 6, 0]} barSize={16} />
      </BarChart>
    </ResponsiveContainer>
  );
}
