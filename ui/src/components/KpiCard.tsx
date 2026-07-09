import { CaretRight } from '@phosphor-icons/react';
import { Skeleton } from 'antd';
import { useState } from 'react';
import type { KpiPalette } from '../colors';

interface KpiCardProps {
  palette: KpiPalette;
  value: number;
  suffix?: string;
  loading?: boolean;
  onClick?: () => void;
}

/** Coastal-palette KPI card: soft gradient + decorative circle accent (.claude/ui_design.md). Optionally clickable. */
export function KpiCard({ palette, value, suffix, loading, onClick }: KpiCardProps) {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) onClick();
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: 'relative',
        overflow: 'hidden',
        background: palette.background,
        border: `1px solid ${palette.border}`,
        borderRadius: 10,
        padding: '14px 16px',
        height: '100%',
        cursor: onClick ? 'pointer' : undefined,
        boxShadow: hovered && onClick ? '0 6px 16px rgba(16,42,67,.10)' : 'none',
        transform: hovered && onClick ? 'translateY(-2px)' : 'none',
        transition: 'box-shadow 0.15s ease, transform 0.15s ease',
      }}
    >
      <div
        style={{
          position: 'absolute',
          right: -24,
          top: -29,
          width: 74,
          height: 74,
          borderRadius: '50%',
          background: palette.circle,
          opacity: 0.65,
        }}
      />
      <div style={{ position: 'relative', zIndex: 1 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: '#5D6F7E',
            marginBottom: 4,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          {palette.label}
          {onClick && <CaretRight size={12} color={hovered ? palette.valueColor : '#98A9BC'} />}
        </div>
        {loading ? (
          <Skeleton.Input active size="small" style={{ width: 60 }} />
        ) : (
          <div
            style={{
              fontSize: 26,
              fontWeight: 700,
              letterSpacing: '-0.02em',
              color: palette.valueColor,
            }}
          >
            {value}
            {suffix}
          </div>
        )}
      </div>
    </div>
  );
}
