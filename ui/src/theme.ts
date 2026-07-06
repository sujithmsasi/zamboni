// Zamboni Arctic Blue — final approved design system (.claude/ui_design.md).
// Supersedes the Phase 3 starting-point tokens; structure/keys mirror the
// approved theme config exactly.
export const zamboniTheme = {
  token: {
    colorPrimary: '#167D9A',
    colorInfo: '#175CD3',
    colorSuccess: '#027A48',
    colorWarning: '#B54708',
    colorError: '#B42318',

    colorText: '#172B4D',
    colorTextSecondary: '#667085',

    colorBgLayout: '#F7F9FC',
    colorBgContainer: '#FFFFFF',
    colorBorder: '#D9E2EC',

    borderRadius: 8,
    controlHeight: 36,

    fontFamily:
      '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },

  components: {
    Layout: {
      siderBg: '#102A43',
      headerBg: '#FFFFFF',
      bodyBg: '#F7F9FC',
    },

    Menu: {
      darkItemBg: '#102A43',
      darkItemColor: '#D8E8F2',
      darkItemHoverBg: '#1E4E70',
      darkItemHoverColor: '#FFFFFF',
      darkItemSelectedBg: '#183F5D',
      darkItemSelectedColor: '#FFFFFF',
      // Tuned so all 13 nav items + 5 group labels fit one viewport height
      // without the sidebar needing its own scrollbar (which, on Windows'
      // classic non-overlay scrollbars, was stealing layout width and
      // truncating labels — see decisions.md) — loosened back up from an
      // over-tightened first pass that felt cramped.
      itemHeight: 36,
      itemMarginBlock: 3,
      groupTitleLineHeight: 1.4,
    },

    Table: {
      headerBg: '#EEF4F8',
      headerColor: '#334E68',
      rowHoverBg: '#F1F7FB',
      borderColor: '#D9E2EC',
    },

    Card: {
      borderRadiusLG: 10,
    },

    Button: {
      primaryShadow: '0 4px 12px rgba(22,125,154,.18)',
    },
  },
} as const;
