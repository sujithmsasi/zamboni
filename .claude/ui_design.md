You are updating the UI theme for a React 18 + TypeScript + Vite application named Zamboni. The app uses Ant Design v5, TanStack Query, react-router-dom, and should use @phosphor-icons/react for sidebar icons.

Goal: implement the final approved Zamboni enterprise design system across the application.

Zamboni is a table-maintenance and data-governance platform. The visual tone must feel calm, polished, premium, spacious, and operationally trustworthy—not like a generic colorful dashboard.

Do not redesign information architecture or change functionality. Update styling, Ant Design theme tokens, layout surfaces, sidebar behavior, typography, iconography, cards, tables, status tags, and safety messaging only.

==================================================
CORE DESIGN DIRECTION
==================================================

Theme name: Zamboni Arctic Blue

Visual balance:
- Deep Arctic Navy sidebar
- Bright white content surfaces
- Soft icy-blue/gray page background
- Muted pastel KPI cards
- Teal for primary actions
- Mint green reserved for safety/reassurance surfaces, especially DRY RUN
- Steel-blue sidebar hover
- Ice-blue active indicators
- No rainbow navigation icons
- No mint-green sidebar hover or active states

The product should feel like a mature enterprise platform for engineers and governance users.

Avoid:
- Heavy shadows
- Neon colors
- Overly rounded components
- Excessively bold text
- Loud gradients
- Generic “dashboard template” styling
- Mixing multiple unrelated icon styles

==================================================
TYPOGRAPHY
==================================================

Use Inter as the global application font.

Font stack:

"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif

Use:
- 400 for body text
- 500 or 600 for navigation, labels, table metadata, buttons
- 700 for page headings, section titles, and KPI values
- Avoid 800, 850, 900 weights across the UI

Typography guidelines:
- Page title: 26–28px, weight 700, line-height 1.15, letter-spacing around -0.02em to -0.03em
- Section title: 16px, weight 700
- KPI label: 12px, weight 600
- KPI value: 25–28px, weight 700, letter-spacing around -0.02em
- Sidebar item: 13px, weight 600
- Table/body text: 13–14px, weight 400–500
- Sidebar section label: 10px, uppercase, weight 700, letter-spacing around 0.09em

Typography should feel open and breathable. Avoid compressed line height and dense bold styling.

Use a self-hosted Inter package if possible:

npm install @fontsource/inter

Then import it once in the application entry point.

==================================================
GLOBAL COLOR TOKENS
==================================================

Use these values:

--navy-950: #102A43;
--navy-900: #173B5D;
--navy-active: #183F5D;
--navy-hover: #1E4E70;

--teal-700: #0E6178;
--teal-600: #167D9A;
--teal-100: #D6F1F5;
--teal-50: #E8F7FA;

--ice-bg: #F7F9FC;
--surface: #FFFFFF;
--frost: #EEF4F8;
--border: #D9E2EC;

--ink: #172B4D;
--text-secondary: #667085;
--text-muted: #5D6F7E;

--success: #027A48;
--success-bg: #ECFDF3;

--warning: #B54708;
--warning-bg: #FFFAEB;

--error: #B42318;
--error-bg: #FEF3F2;

--info: #175CD3;
--info-bg: #EFF8FF;

==================================================
SIDEBAR — FINAL APPROVED DIRECTION
==================================================

Use a deep Arctic Navy sidebar.

Sidebar base:
- Background: #102A43
- Optional subtle gradient:
  linear-gradient(180deg, #102A43 0%, #123653 100%)
- Border-right:
  1px solid rgba(255,255,255,.10)

Sidebar text:
- Default text: #D8E8F2
- Section label: #8CB0C9
- Brand subtitle: #B8D3E4

Keep the sidebar dark. Do not use a white sidebar.

==================================================
LOGO / BRAND AREA
==================================================

Use the existing logo asset through the Vite asset pipeline.

Do not hardcode a Windows filesystem path inside an img src.

Example:

import zamboniLogo from '../assets/zamboni_logo.png';

Use a compact logo tile:
- 43px x 43px
- Background: rgba(255,255,255,.10)
- Border: 1px solid rgba(214,241,245,.38)
- Radius: 12px
- The logo should be centered and strongly visible against the navy sidebar

Brand text:
- “Zamboni”: white, Inter 700, around 18px
- Subtitle “TABLE MAINTENANCE”: #B8D3E4, uppercase, 9px, weight 600, letter spacing around .10em

Use a subtle divider under the brand:
- border-bottom: 1px solid rgba(255,255,255,.14)

==================================================
SIDEBAR MENU STATES
==================================================

Default item:
- Background: transparent
- Text: #D8E8F2
- Radius: 8px
- Padding around 10px 11px
- Font size: 13px
- Font weight: 600

Hover item:
- Background: #1E4E70
- Text: #FFFFFF
- Box shadow:
  inset 0 0 0 1px rgba(105,183,227,.25),
  0 3px 8px rgba(0,0,0,.12)

Active item:
- Background: #183F5D
- Text/icon: #FFFFFF
- Left active rail:
  inset 3px 0 0 #69B7E3

Important:
- Do not use mint/green for sidebar hover.
- Do not use colorful item backgrounds.
- Sidebar interactions should remain navy, steel blue, white, and ice-blue.

==================================================
ICON SYSTEM — PHOSPHOR DUOTONE
==================================================

Use @phosphor-icons/react.

Install if needed:

npm install @phosphor-icons/react

Use the “duotone” weight for all sidebar icons.

All sidebar icons:
- Size: 18px
- Weight: "duotone"
- Use one consistent icon family only
- Do not mix Ant icons, emoji, random SVGs, or icon packs

Icon mapping:
- Dashboard → SquaresFour
- Tables → Table
- Explorer → MagnifyingGlass
- Jobs → Lightning
- Policies → ShieldCheck
- Reports → ChartBar
- Configuration → Gear

Use this icon behavior:

Default:
- color: #8CB0C9
- fill: #102A43

This makes the icons look like refined muted-blue line icons while retaining Phosphor duotone structure.

Hover:
- color: #FFFFFF
- fill: #69B7E3

Active:
- color: #FFFFFF
- fill: #69B7E3

Important:
- Do not give every icon a random different color.
- No rainbow sidebar icon system.
- The visual hierarchy comes from state, not from unrelated icon colors.
- Default icons should remain muted and unified.
- Hover/active icons should become white stroke with icy-blue duotone fill.

Suggested implementation:

```tsx
import {
  SquaresFour,
  Table,
  MagnifyingGlass,
  Lightning,
  ShieldCheck,
  ChartBar,
  Gear,
} from '@phosphor-icons/react';

const iconProps = (isActiveOrHovered: boolean) => ({
  size: 18,
  weight: 'duotone' as const,
  color: isActiveOrHovered ? '#FFFFFF' : '#8CB0C9',
  fill: isActiveOrHovered ? '#69B7E3' : '#102A43',
});
```

For each navigation item, determine:
- isActive from route state
- isHovered from component state or CSS-driven approach if practical

Example:

```tsx
<SquaresFour {...iconProps(isActive || isHovered)} />
```

Use the same icon behavior for hover and active state.

==================================================
MAIN APPLICATION SHELL
==================================================

Main page background:
- #F7F9FC

Header:
- White background
- Bottom border: #D9E2EC

Content panels/cards:
- White background
- Border: #D9E2EC
- Radius: 10px
- Shadow:
  0 4px 14px rgba(16,42,67,.05)

Avoid heavy drop shadows.

Standard component radii:
- Cards: 10px
- Buttons / inputs: 8px
- Tags: 5px–6px
- Avoid oversized round corners.

==================================================
PRIMARY ACTIONS
==================================================

Primary button:
- Background: #167D9A
- Hover: #0E6178
- Text: white
- Radius: 8px
- Weight: 600 or 700
- Optional shadow:
  0 4px 12px rgba(22,125,154,.18)

Use for:
- Register Table
- Save Changes
- Run Validation
- Apply Policy
- Confirm maintenance configuration

Secondary buttons:
- White background
- Border: #D9E2EC
- Text: #102A43
- Hover background: #F1F7FB

==================================================
DRY RUN BANNER — MINT GREEN SAFETY SURFACE
==================================================

Use a calm mint-green banner for:

“DRY RUN mode — no writes will be executed”

This should feel safe and reassuring, not like a warning.

Approved styling:
- Background:
  linear-gradient(90deg, #ECFDF3 0%, #F5FFF8 100%)
- Border: #B7E6C6
- Text: #176B43
- Radius: 9px
- Padding: 11px 13px
- Shadow:
  0 2px 8px rgba(2,122,72,.05)

Icon:
- Use a small green check icon
- 20px x 20px circular tile
- Tile background: #D1FADF
- Icon color: #027A48

Text treatment:
- “DRY RUN mode” should be semibold/bold
- Remaining message should use normal/medium weight
- Do not use yellow for this banner

Mint is reserved primarily for this safety/reassurance layer and normal success states—not sidebar hover.

==================================================
KPI CARD DESIGN — RELAXED COASTAL PALETTE
==================================================

KPI cards should feel calm, refined, and low-saturation.

Do not use:
- Thick colored top borders
- Loud solid blocks
- Strong gradients
- Neon KPI colors

Each KPI card:
- White-to-soft-tint gradient
- Subtle matching border
- Soft circular decorative accent in the upper-right
- Accent circle approximately 74px x 74px
- Position:
  right: -24px;
  top: -29px;
- Border-radius: 50%
- Opacity around .65
- Text must sit above accent using relative positioning / z-index

Shared KPI typography:
- Label: #5D6F7E, 12px, weight 600
- Value: 25–28px, weight 700
- Keep label/value spacing open and calm

Approved card palette:

1. Tables Registered
- Background:
  linear-gradient(145deg, #F0FBFC 0%, #FFFFFF 72%)
- Border: #CBECEF
- Decorative circle: #BDEAF0
- Value color: #176B78

2. HK Enabled
- Background:
  linear-gradient(145deg, #F2F7FE 0%, #FFFFFF 72%)
- Border: #D5E4F8
- Decorative circle: #C8DDF8
- Value color: #315F9B

3. Coverage
- Background:
  linear-gradient(145deg, #F0FCF6 0%, #FFFFFF 72%)
- Border: #CDEEDB
- Decorative circle: #BEEBD0
- Value color: #237A52

4. Executions Today
- Background:
  linear-gradient(145deg, #FFF9EE 0%, #FFFFFF 72%)
- Border: #F5E2BE
- Decorative circle: #F7DEAB
- Value color: #9A6210

5. Failures (7d)
- Background:
  linear-gradient(145deg, #FFF4F3 0%, #FFFFFF 72%)
- Border: #F5D2CE
- Decorative circle: #F7C8C1
- Value color: #A33A31

==================================================
TABLES
==================================================

Tables are the usability center of Zamboni.

Table styling:
- Header background: #EEF4F8
- Header text: #334E68
- Header font: 10–11px, weight 600/700
- Light uppercase is acceptable for table headers only
- Standard row background: white
- Row hover: #F1F7FB
- Borders: #D9E2EC / #E7EEF3
- General table text: #344054
- Important table names: #172B4D, weight 600
- Padding: roughly 12–13px vertical and 16px horizontal
- Tables should remain horizontally scrollable on narrow screens

==================================================
STATUS TAGS
==================================================

Use small soft-filled tags.

Do not use loud outlined tags or aggressive saturated pills.

Success:
- Background: #ECFDF3
- Text: #027A48
- Border: #ABEFC6

Failure:
- Background: #FEF3F2
- Text: #B42318
- Border: #FECDCA

Skipped / Warning:
- Background: #FFFAEB
- Text: #B54708
- Border: #FEDF89

Running / Info:
- Background: #EFF8FF
- Text: #175CD3
- Border: #B2DDFF

Neutral / Pending:
- Background: #F2F4F7
- Text: #475467
- Border: #D5DCE5

==================================================
ANT DESIGN V5 THEME TOKENS
==================================================

Create or update a central Ant Design theme configuration similar to this:

```ts
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
};
```

Note: Ant Design Menu tokens alone may not be enough to achieve the exact sidebar icon hover/active behavior. Add scoped sidebar CSS or component-level styles where needed.

==================================================
RESPONSIVENESS
==================================================

Preserve responsive behavior:
- Sidebar should collapse or hide cleanly on small screens
- KPI cards should reflow:
  5 columns → 3 columns → 2 columns → 1 column
- Tables should scroll horizontally when needed
- Do not compromise readability on tablet or mobile

==================================================
IMPLEMENTATION REQUIREMENTS
==================================================

1. Do not modify:
   - API calls
   - TanStack Query behavior
   - routing
   - data models
   - existing business logic

2. Use Vite asset imports for the logo.

3. Centralize colors in:
   - CSS variables
   - a token module
   - Ant Design theme tokens
   - or a combination of these

4. Use Inter consistently throughout the app.

5. Use @phosphor-icons/react with duotone icons for sidebar navigation.

6. Do not use mint/green for sidebar hover or active states.

7. Do not use a white sidebar.

8. Keep the main content area light; only sidebar/navigation is dark.

9. Maintain accessible contrast for:
   - sidebar menu states
   - icon states
   - buttons
   - status tags
   - dry run banner
   - table text

10. Do not redesign page structure. This is a visual system upgrade.

At the end, provide:
- Files changed
- npm packages added
- Theme/token files created or updated
- A concise explanation of sidebar behavior
- A concise explanation of icon behavior
- A concise explanation of typography and KPI card decisions
- Any implementation assumptions
