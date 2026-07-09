import {
  ArrowsClockwise,
  BookOpen,
  ChartBar,
  FileText,
  Flask,
  Gear,
  Heartbeat,
  House,
  Lightning,
  LinkSimple,
  MagnifyingGlass,
  ShieldCheck,
  SquaresFour,
  Table as TableIcon,
  Trash,
  type Icon,
} from '@phosphor-icons/react';
import type { ReactNode } from 'react';
import AppGuidePage from './pages/AppGuide';
import AuditLogPage from './pages/AuditLog';
import ControlMIntegrationPage from './pages/ControlMIntegration';
import CostReportPage from './pages/CostReport';
import DomainManagementPage from './pages/DomainManagement';
import DryRunViewerPage from './pages/DryRunViewer';
import ExecutionLogPage from './pages/ExecutionLog';
import HealthDashboardPage from './pages/HealthDashboard';
import HomePage from './pages/Home';
import LiveActivityPage from './pages/LiveActivity';
import NonProdLifecyclePage from './pages/NonProdLifecycle';
import PolicyConfigPage from './pages/PolicyConfig';
import SettingsPage from './pages/Settings';
import StaleResourcesPage from './pages/StaleResources';
import TableRegistrationPage from './pages/TableRegistration';

export interface RouteMeta {
  path: string;
  label: string;
  icon: Icon;
  color: string;
  element: ReactNode;
}

export interface RouteCategory {
  label: string;
  routes: RouteMeta[];
}

// The 13 routes per contracts.md §7, grouped into sidebar sections, plus
// one additive 14th (/help, the in-app App Guide -- added post-replatform,
// not part of the original contract). All are fully built as of Phase 5b
// -- UI is feature-complete, Streamlit is fallback-only. Icons: Phosphor
// duotone (.claude/ui_design.md). `color` tints the icon per category (one
// hue per section, not per-icon) — see App.tsx for how it's applied.
export const ROUTE_CATEGORIES: RouteCategory[] = [
  {
    label: 'Overview',
    routes: [{ path: '/', label: 'Home', icon: House, color: '#69B7E3', element: <HomePage /> }],
  },
  {
    label: 'Registry',
    routes: [
      {
        path: '/domains',
        label: 'Domain Management',
        icon: SquaresFour,
        color: '#4FD1C5',
        element: <DomainManagementPage />,
      },
      {
        path: '/tables',
        label: 'Table Registration',
        icon: TableIcon,
        color: '#4FD1C5',
        element: <TableRegistrationPage />,
      },
      {
        path: '/controlm',
        label: 'Control-M Integration',
        icon: LinkSimple,
        color: '#4FD1C5',
        element: <ControlMIntegrationPage />,
      },
      {
        path: '/policies',
        label: 'Policy Configuration',
        icon: ShieldCheck,
        color: '#4FD1C5',
        element: <PolicyConfigPage />,
      },
    ],
  },
  {
    label: 'Monitoring',
    routes: [
      {
        path: '/health',
        label: 'Health Dashboard',
        icon: Heartbeat,
        color: '#F2B84B',
        element: <HealthDashboardPage />,
      },
      {
        path: '/activity',
        label: 'Live Activity',
        icon: Lightning,
        color: '#F2B84B',
        element: <LiveActivityPage />,
      },
      {
        path: '/executions',
        label: 'Execution Log',
        icon: FileText,
        color: '#F2B84B',
        element: <ExecutionLogPage />,
      },
      {
        path: '/costs',
        label: 'Cost Report',
        icon: ChartBar,
        color: '#F2B84B',
        element: <CostReportPage />,
      },
    ],
  },
  {
    label: 'Governance & Safety',
    routes: [
      {
        path: '/dryrun',
        label: 'Dry Run Viewer',
        icon: Flask,
        color: '#6FCF97',
        element: <DryRunViewerPage />,
      },
      {
        path: '/nonprod',
        label: 'Non-Prod Lifecycle',
        icon: ArrowsClockwise,
        color: '#6FCF97',
        element: <NonProdLifecyclePage />,
      },
      {
        path: '/stale',
        label: 'Stale Resources',
        icon: Trash,
        color: '#6FCF97',
        element: <StaleResourcesPage />,
      },
    ],
  },
  {
    label: 'Administration',
    routes: [
      {
        path: '/settings',
        label: 'Settings',
        icon: Gear,
        color: '#B197FC',
        element: <SettingsPage />,
      },
      {
        path: '/audit',
        label: 'Audit Log',
        icon: MagnifyingGlass,
        color: '#B197FC',
        element: <AuditLogPage />,
      },
      {
        path: '/help',
        label: 'App Guide',
        icon: BookOpen,
        color: '#B197FC',
        element: <AppGuidePage />,
      },
    ],
  },
];

// Flat list — for route matching / <Route> rendering, which don't care about grouping.
export const ROUTES: RouteMeta[] = ROUTE_CATEGORIES.flatMap((c) => c.routes);
