import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { CostTrendPoint } from '../../../api/types';

interface CostTrendChartProps {
  data: CostTrendPoint[];
}

/** Estimated daily Athena scan cost (same $5/TB flat-rate convention as the Cost Report). */
export function CostTrendChart({ data }: CostTrendChartProps) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="#EEF4F8" vertical={false} />
        <XAxis dataKey="execution_date" tick={{ fontSize: 11, fill: '#667085' }} tickFormatter={(d: string) => d.slice(5)} />
        <YAxis tick={{ fontSize: 11, fill: '#667085' }} tickFormatter={(v) => `$${v}`} />
        <Tooltip
          formatter={(value) => [`$${value}`, 'Est. Athena cost']}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }}
        />
        <Bar dataKey="cost_usd" fill="#175CD3" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
