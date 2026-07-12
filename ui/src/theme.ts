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
      // Tuned so all nav items + group labels fit one viewport height
      // without the sidebar needing its own scrollbar (which, on Windows'
      // classic non-overlay scrollbars, was stealing layout width and
      // truncating labels — see decisions.md). Re-tightened 2026-07-11
      // when the 14th route (/help, App Guide) pushed both 1366x768
      // (79px) and 1280x720 (27px) past fitting (measured via Playwright,
      // not guessed) — not as loose as the original 13-item tuning, but
      // stopped short of the earlier "felt cramped" over-tightened pass
      // (verified via screenshot at both sizes, not just the numbers).
      itemHeight: 30,
      itemMarginBlock: 1,
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
