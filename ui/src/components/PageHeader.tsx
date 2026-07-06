interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}

export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        marginBottom: 20,
      }}
    >
      <div>
        <div
          style={{
            fontSize: 27,
            fontWeight: 700,
            lineHeight: 1.15,
            letterSpacing: '-0.025em',
            color: '#172B4D',
          }}
        >
          {title}
        </div>
        {subtitle && (
          <div style={{ fontSize: 13, fontWeight: 500, color: '#667085', marginTop: 4 }}>{subtitle}</div>
        )}
      </div>
      {actions && <div>{actions}</div>}
    </div>
  );
}
