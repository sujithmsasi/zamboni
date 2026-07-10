/**
 * App Guide content — one entry per route (contracts.md §7's 13 routes),
 * grouped into the same 5 sidebar categories as routes.tsx. Deliberately a
 * standalone data file, not imported from routes.tsx, to avoid a circular
 * import (routes.tsx has to import this page's component to route to it).
 * Category label/color are duplicated here in sync with routes.tsx by hand
 * -- five short entries, not worth a shared module for.
 */

export interface GuideSection {
  title: string;
  items: string[];
}

export interface GuidePage {
  path: string;
  label: string;
  summary: string;
  sections?: GuideSection[];
}

export interface GuideCategory {
  label: string;
  color: string;
  pages: GuidePage[];
}

export const GUIDE: GuideCategory[] = [
  {
    label: 'Overview',
    color: '#69B7E3',
    pages: [
      {
        path: '/',
        label: 'Home',
        summary:
          'The fleet-wide landing page — five KPI cards, two trend charts, and a governance status ' +
          'card, all clickable for drill-down detail.',
        sections: [
          {
            title: 'What you can do here',
            items: [
              'Click any KPI card (Registered Tables, HK Coverage, Executions Today, Failures 7d, Storage Saved) to open a drill-down grid of the underlying rows.',
              'Fleet Coverage by Domain and Execution Trend charts give a quick visual read on where housekeeping is and isn’t running.',
              'The Governance card turns amber/red the moment there’s a dual-optimizer conflict or an active lock — click through to Health Dashboard for the full report.',
              'Recent Activity at the bottom is the same execution feed as Live Activity, just a shorter, static snapshot.',
            ],
          },
        ],
      },
    ],
  },
  {
    label: 'Registry',
    color: '#4FD1C5',
    pages: [
      {
        path: '/domains',
        label: 'Domain Management',
        summary:
          'Register and manage the top-level domains every table belongs to — the org unit each ' +
          'table’s ownership, retention defaults, and escalation routing hang off of.',
        sections: [
          {
            title: 'What you can do here',
            items: [
              'Register a new domain: owner email, hot/archive/stale-threshold retention days, digest settings.',
              'Edit an existing domain — the same fields, plus is_active (the fleet-wide kill switch: setting a domain inactive stops HK/Archival/Lifecycle from touching any of its tables) and a read-only preview of where its escalation/digest notifications route to.',
              'Table count per domain is shown live, not cached.',
            ],
          },
        ],
      },
      {
        path: '/tables',
        label: 'Table Registration',
        summary: 'Discover Iceberg tables from the Glue catalog, register them, and manage their engine flags.',
        sections: [
          {
            title: 'Browse & Register',
            items: [
              'Pick a Glue database (or "Show all tables"), select one or more unregistered tables, and register them in bulk.',
              'A policy template is auto-inferred and applied from the table’s layer/tier at registration time — the confirmation shows exactly which template was applied.',
            ],
          },
          {
            title: 'Registered Tables',
            items: [
              'Full grid of every registered table — search by name, filter by domain/layer/tier, HK-enabled-only toggle, CSV export.',
            ],
          },
          {
            title: 'Edit Table',
            items: [
              'Search and select any registered table to edit domain/layer/tier/owner/CI number, the HK/Archival/Lifecycle engine flags, processing cadence, and its Control-M integration fields (job name, HK job, Gate 1 job, start time, duration).',
            ],
          },
          {
            title: 'Engine Flags',
            items: [
              'Single-table and bulk (filtered by domain/layer/database) toggles for HK/Archival/Lifecycle enablement — the fast path when you just need to flip a flag without opening the full edit form.',
            ],
          },
        ],
      },
      {
        path: '/controlm',
        label: 'Control-M Integration',
        summary: 'Manage the Control-M job registry and assign job names to tables, individually or in bulk.',
        sections: [
          {
            title: 'Control-M Job Registry',
            items: [
              'Job List — search/filter registered jobs, see how many tables reference each one, edit or remove entries.',
              'Add Single Job — register one job’s metadata (type, domain, expected start time/duration/frequency) by hand.',
              'Bulk Upload CSV — upload a job list, review the parsed rows (select/deselect), then save.',
            ],
          },
          {
            title: 'Manual Bulk Apply',
            items: [
              'Pick target tables by domain/layer/database/name pattern, confirm the exact selection in a preview, then assign one Control-M job to all of them at once.',
              'Selecting an existing job from the autocomplete autofills its start time/duration/frequency; typing a new name registers it fresh.',
            ],
          },
          {
            title: 'CSV Workflow',
            items: [
              'Step 1: download a job-mapping template (pre-filled with your registered tables).',
              'Step 2: fill in the Control-M job column per row and upload it back — a dry-run preview shows exactly what will change before you commit.',
            ],
          },
        ],
      },
      {
        path: '/policies',
        label: 'Policy Configuration',
        summary: 'View and edit each table’s housekeeping policy — compaction, retention, and the three gates.',
        sections: [
          {
            title: 'View Configs',
            items: [
              'Full grid of every table’s current policy — strategy, target file size, snapshot/orphan retention, run frequency, gate 1/2/3 state, template-vs-override status. Search by table name, CSV export.',
            ],
          },
          {
            title: 'Edit Single Table',
            items: [
              'An accordion of Gates / Window & Blackout / Compaction — expand one at a time, each collapsed section shows a status summary so you don’t lose context.',
              'Gate 0 override lets you temporarily bypass the AWS-optimizer-conflict check for a specific table, with a required reason and a capped expiry.',
            ],
          },
          {
            title: 'Bulk Apply Template',
            items: [
              'Apply a named policy template to every table in a domain/layer at once. "Skip manually overridden tables" (on by default) protects any table someone has already hand-tuned from being silently reset.',
            ],
          },
          {
            title: 'Templates',
            items: [
              'View, edit, add, or delete the named policy templates tables get their defaults from at registration time (e.g. STAGING_DEFAULT, CRITICAL_HIGH_VOL). Built-in templates can’t be deleted; templates currently in use are protected too.',
            ],
          },
        ],
      },
    ],
  },
  {
    label: 'Monitoring',
    color: '#F2B84B',
    pages: [
      {
        path: '/health',
        label: 'Health Dashboard',
        summary: 'Fleet-wide health, cost, and governance in one place.',
        sections: [
          {
            title: 'What’s on this page',
            items: [
              'Storage Reclaimed trend and Top Tables by Reclaim — where compaction/vacuum is actually paying off.',
              'Estimated Athena Cost trend, and a Fleet Health Scorecard (healthy / needs attention / at risk) with a grid of the unhealthy tables.',
              'Non-Prod Lifecycle Funnel and Dry-Run Adoption — how many tables are still ramping up before going fully live.',
              'Maintenance Governance section: the dual-optimizer conflict report (tables where both Zamboni and AWS’s native optimizer are configured — a real risk, not cosmetic), a "Rescan conflicts" button, and recent integrity check failures from the last 7 days.',
            ],
          },
        ],
      },
      {
        path: '/activity',
        label: 'Live Activity',
        summary: 'What’s running right now, refreshed automatically every 10 seconds.',
        sections: [
          {
            title: 'What’s on this page',
            items: [
              'Currently Running grid — every in-flight execution, live.',
              'Recent Operations, filterable by engine/status.',
              'Active Maintenance Locks strip, with a force-release action for stuck locks (admin override — use with care, it bypasses the normal lock-owner check).',
            ],
          },
        ],
      },
      {
        path: '/executions',
        label: 'Execution Log',
        summary: 'The full history of every HK, Archival, and Lifecycle run.',
        sections: [
          {
            title: 'What you can do here',
            items: [
              'Filter by table/domain/engine/status/date range.',
              'Expand any row for full detail (before/after metadata, snapshot ids, integrity status) without leaving the grid.',
              'CSV export of the current filtered view.',
            ],
          },
        ],
      },
      {
        path: '/costs',
        label: 'Cost Report',
        summary: 'Estimated Athena spend, grouped by domain, layer, or tier over a selected period.',
        sections: [
          {
            title: 'What you can do here',
            items: [
              'Switch the grouping axis (domain/layer/tier) and the time period.',
              'A banner up top tells you whether the numbers are live billing data or a flat-rate estimate — worth checking before quoting a figure to anyone.',
            ],
          },
        ],
      },
    ],
  },
  {
    label: 'Governance & Safety',
    color: '#6FCF97',
    pages: [
      {
        path: '/dryrun',
        label: 'Dry Run Viewer',
        summary: 'Simulate the HK Engine on any table without writing anything.',
        sections: [
          {
            title: 'What you can do here',
            items: [
              'Search for any registered table and see its full gate evaluation — upstream job status, window/blackout decision, circuit breaker state — exactly as the engine would evaluate it right now.',
              'For bin-pack tables, the actual OPTIMIZE SQL that would run is shown, with a copy button — useful for sanity-checking a policy change before it goes live.',
            ],
          },
        ],
      },
      {
        path: '/nonprod',
        label: 'Non-Prod Lifecycle',
        summary: 'Discover and manage stale non-production tables working toward automatic cleanup.',
        sections: [
          {
            title: 'State Overview',
            items: [
              'Per-state counts — Active, Stale Candidate, Greenzone, Pending Drop — with the actual threshold days each transition uses, so the states aren’t a mystery.',
            ],
          },
          {
            title: 'Bulk Exemption / Claim',
            items: [
              'Select multiple stale/pending tables at once and exempt (stop the countdown) or claim (mark someone’s actively using it) them with one shared reason.',
            ],
          },
          {
            title: 'Single Table Action',
            items: ['Same exempt/claim actions, for one table at a time via search.'],
          },
          {
            title: 'Deletion History',
            items: [
              'A 90-day record of what was actually dropped, plus total storage reclaimed — the audit trail for "wait, what happened to that table."',
            ],
          },
        ],
      },
      {
        path: '/stale',
        label: 'Stale Resources',
        summary: 'Four different flavors of "this needs attention," each surfaced separately.',
        sections: [
          {
            title: 'The four tabs',
            items: [
              'Stale HK — tables whose housekeeping hasn’t run within their expected threshold.',
              'S3 Orphans — S3 prefixes with no matching registered table, worth a manual look.',
              'Zero-Row — tables whose archival runs are consistently moving zero rows (may mean the job is misconfigured, not that there’s nothing to archive).',
              'NonProd Stale — a cross-reference into Non-Prod Lifecycle’s stale states, scoped to this view.',
            ],
          },
        ],
      },
    ],
  },
  {
    label: 'Administration',
    color: '#B197FC',
    pages: [
      {
        path: '/settings',
        label: 'Settings',
        summary: 'Platform-wide configuration — defaults, enforcement, escalation routing, and advanced tuning.',
        sections: [
          {
            title: 'The four tabs',
            items: [
              'General — platform-wide defaults.',
              'Enforcement — safety-floor and policy-enforcement toggles.',
              'Escalation Matrix — who gets notified for which domain when something needs attention; full add/edit/delete.',
              'Advanced — backup file-pattern rules, Teams webhook (URL is masked once saved), Cost Explorer config, SSO/LDAP status, control-plane sync/backup intervals, and the active maintenance locks strip (same force-release action as Live Activity).',
            ],
          },
        ],
      },
      {
        path: '/audit',
        label: 'Audit Log',
        summary: 'Every mutating action taken through the app — who, what, when, dry-run or real.',
        sections: [
          {
            title: 'What you can do here',
            items: [
              'Filter by time range/action/actor; status filter narrows the currently-loaded page.',
              'Expand any row to see the full before/after value — this is the record of "who changed this and what did it look like before."',
            ],
          },
        ],
      },
    ],
  },
];
