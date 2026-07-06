import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { CoverageByDomain } from '../../../api/types';

interface CoverageByDomainChartProps {
  data: CoverageByDomain[];
}

function colorFor(pct: number): string {
  if (pct >= 80) return '#027A48';
  if (pct >= 50) return '#B54708';
  return '#B42318';
}

/** HK-enabled coverage % per domain — the VP-level "who's actually protected" view. */
export function CoverageByDomainChart({ data }: CoverageByDomainChartProps) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(180, data.length * 44)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
        <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fontSize: 11, fill: '#667085' }} />
        <YAxis type="category" dataKey="domain" width={100} tick={{ fontSize: 12, fill: '#344054' }} />
        <Tooltip
          formatter={(value, _name, item) => [
            `${value}% (${(item.payload as CoverageByDomain).enabled}/${(item.payload as CoverageByDomain).total} tables)`,
            'HK coverage',
          ]}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }}
        />
        <Bar dataKey="pct_enabled" radius={[0, 6, 6, 0]} barSize={20}>
          {data.map((entry) => (
            <Cell key={entry.domain} fill={colorFor(entry.pct_enabled)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
