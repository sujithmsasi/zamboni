import { CheckCircle, Warning, WarningOctagon } from '@phosphor-icons/react';
import type { Icon } from '@phosphor-icons/react';
import { Link } from 'react-router-dom';
import { useHealthKpis } from '../api/hooks/useHealth';
import { colors } from '../colors';

interface Tone {
  bg: string;
  border: string;
  text: string;
  iconBg: string;
  icon: Icon;
  iconColor: string;
}

const HEALTHY: Tone = {
  bg: 'linear-gradient(90deg, #ECFDF3 0%, #F5FFF8 100%)',
  border: '#B7E6C6',
  text: '#176B43',
  iconBg: '#D1FADF',
  icon: CheckCircle,
  iconColor: colors.success,
};

const NEEDS_ATTENTION: Tone = {
  bg: 'linear-gradient(90deg, #FFFAEB 0%, #FFFDF6 100%)',
  border: '#FEDF89',
  text: '#8A5B12',
  iconBg: '#FEF0C7',
  icon: Warning,
  iconColor: colors.warning,
};

const AT_RISK: Tone = {
  bg: 'linear-gradient(90deg, #FEF3F2 0%, #FFFBFA 100%)',
  border: '#FECDCA',
  text: colors.error,
  iconBg: '#FEE4E2',
  icon: WarningOctagon,
  iconColor: colors.error,
};

/** Fleet-wide engine health strip, shown at the top of every page --
 * replaces the previous always-on dry-run disclaimer with something
 * actionable: at-a-glance fleet health, one click from the full report. */
export function FleetHealthBanner() {
  const { data, isLoading } = useHealthKpis();
  if (isLoading || !data) return null;

  const { healthy, needs_attention, at_risk } = data.fleet_health;
  const total = healthy + needs_attention + at_risk;
  if (total === 0) return null;

  const tone = at_risk > 0 ? AT_RISK : needs_attention > 0 ? NEEDS_ATTENTION : HEALTHY;
  const IconComp = tone.icon;

  const parts: string[] = [`${healthy} healthy`];
  if (needs_attention > 0) parts.push(`${needs_attention} needs attention`);
  if (at_risk > 0) parts.push(`${at_risk} at risk`);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 9,
        margin: '12px 16px 0',
        padding: '11px 13px',
        boxShadow: '0 2px 8px rgba(2,122,72,.05)',
      }}
    >
      <span
        style={{
          width: 20,
          height: 20,
          borderRadius: '50%',
          background: tone.iconBg,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <IconComp size={14} weight="fill" color={tone.iconColor} />
      </span>
      <span style={{ fontSize: 13, color: tone.text }}>
        <strong style={{ fontWeight: 600 }}>Fleet Health</strong> — {parts.join(' · ')}
      </span>
      <Link to="/health" style={{ fontSize: 12, color: tone.text, textDecoration: 'underline' }}>
        View report →
      </Link>
    </div>
  );
}
