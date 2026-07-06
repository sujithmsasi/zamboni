import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { ExecutionTrendPoint } from '../../../api/types';

interface ExecutionTrendChartProps {
  data: ExecutionTrendPoint[];
}

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: '#027A48',
  FAILURE: '#B42318',
  SKIPPED: '#B54708',
  DRY_RUN: '#175CD3',
};

const STATUS_ORDER = ['SUCCESS', 'SKIPPED', 'FAILURE', 'DRY_RUN'];

function pivot(rows: ExecutionTrendPoint[]) {
  const byDate = new Map<string, Record<string, number | string>>();
  for (const row of rows) {
    const entry = byDate.get(row.execution_date) ?? { execution_date: row.execution_date };
    entry[row.status] = row.count;
    byDate.set(row.execution_date, entry);
  }
  return [...byDate.values()].sort((a, b) => String(a.execution_date).localeCompare(String(b.execution_date)));
}

/** Daily execution volume by outcome, last 14 days — the VP-level "is HK healthy" trend. */
export function ExecutionTrendChart({ data }: ExecutionTrendChartProps) {
  const pivoted = pivot(data);
  const statuses = STATUS_ORDER.filter((s) => data.some((r) => r.status === s));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={pivoted} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="#EEF4F8" vertical={false} />
        <XAxis
          dataKey="execution_date"
          tick={{ fontSize: 11, fill: '#667085' }}
          tickFormatter={(d: string) => d.slice(5)}
        />
        <YAxis tick={{ fontSize: 11, fill: '#667085' }} allowDecimals={false} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {statuses.map((status) => (
          <Area
            key={status}
            type="monotone"
            dataKey={status}
            stackId="1"
            stroke={STATUS_COLORS[status]}
            fill={STATUS_COLORS[status]}
            fillOpacity={0.25}
            name={status}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
