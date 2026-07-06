import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import type { FleetHealth } from '../../../api/types';

interface FleetHealthScorecardProps {
  data: FleetHealth;
}

const COLORS = { Healthy: '#027A48', 'Needs Attention': '#B54708', 'At Risk': '#B42318' };

/** Proxy health classification (not a live per-table Iceberg check — see decisions.md). */
export function FleetHealthScorecard({ data }: FleetHealthScorecardProps) {
  const chartData = [
    { name: 'Healthy', value: data.healthy },
    { name: 'Needs Attention', value: data.needs_attention },
    { name: 'At Risk', value: data.at_risk },
  ].filter((d) => d.value > 0);

  const total = data.healthy + data.needs_attention + data.at_risk;

  return (
    <div style={{ position: 'relative' }}>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie data={chartData} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={2}>
            {chartData.map((entry) => (
              <Cell key={entry.name} fill={COLORS[entry.name as keyof typeof COLORS]} />
            ))}
          </Pie>
          <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #D9E2EC' }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
      <div
        style={{
          position: 'absolute',
          top: '38%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          textAlign: 'center',
          pointerEvents: 'none',
        }}
      >
        <div style={{ fontSize: 22, fontWeight: 700, color: '#172B4D' }}>{total}</div>
        <div style={{ fontSize: 11, color: '#667085' }}>HK-enabled</div>
      </div>
    </div>
  );
}
