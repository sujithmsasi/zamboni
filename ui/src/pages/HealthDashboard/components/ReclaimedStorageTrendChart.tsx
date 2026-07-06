import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { ReclaimedStoragePoint } from '../../../api/types';

interface ReclaimedStorageTrendChartProps {
  data: ReclaimedStoragePoint[];
}

/** GB genuinely freed per day — vacuum orphan/snapshot removal + archival move-to-cold-tier. */
export function ReclaimedStorageTrendChart({ data }: ReclaimedStorageTrendChartProps) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="#EEF4F8" vertical={false} />
        <XAxis dataKey="day" tick={{ fontSize: 11, fill: '#667085' }} tickFormatter={(d: string) => d.slice(5)} />
        <YAxis tick={{ fontSize: 11, fill: '#667085' }} tickFormatter={(v) => `${v} GB`} />
        <Tooltip
          formatter={(value) => `${value} GB`}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area
          type="monotone"
          dataKey="vacuum_gb"
          stackId="1"
          name="Vacuum (orphan/snapshot)"
          stroke="#167D9A"
          fill="#167D9A"
          fillOpacity={0.3}
        />
        <Area
          type="monotone"
          dataKey="archived_gb"
          stackId="1"
          name="Archived (cold tier)"
          stroke="#B54708"
          fill="#B54708"
          fillOpacity={0.3}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
