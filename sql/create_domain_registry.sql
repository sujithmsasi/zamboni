-- =============================================================================
-- Domain Registry
-- Tracks all registered domains. New domains added via Streamlit app or CLI.
-- Seeded with known domains on first deploy.
-- Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS zamboni_catalog.domain_registry (

    domain_name         STRING      COMMENT 'Domain identifier — lowercase, no spaces (e.g. finance, ers)',
    display_name        STRING      COMMENT 'Human-readable name (e.g. Finance, ERS)',
    description         STRING      COMMENT 'What this domain covers',

    -- Ownership
    owner_name          STRING      COMMENT 'Domain owner full name',
    owner_email         STRING      COMMENT 'Domain owner email / distribution list',
    team_name           STRING      COMMENT 'Team responsible for this domain',

    -- Archive policy (overrides config/domain_retention.json for registered domains)
    archive_enabled     BOOLEAN     COMMENT 'Enable archival for staging tables in this domain',
    hot_retention_days  INT         COMMENT 'Days to retain data in staging before archival',
    archive_duration_days INT       COMMENT 'How long archived data is retained',

    -- Lifecycle policy
    stale_threshold_days    INT     COMMENT 'Days of inactivity before non-prod table flagged as STALE_CANDIDATE',
    auto_delete_after_days  INT     COMMENT 'Hard ceiling — non-prod tables deleted after this many days',

    -- Status
    is_active           BOOLEAN     COMMENT 'False = domain decommissioned, tables still tracked but no new HK',

    -- Weekly digest settings
    digest_enabled       BOOLEAN COMMENT 'true = include this domain in weekly HK digest email',
    digest_email         STRING  COMMENT 'Digest recipient email (defaults to owner_email if blank)',
    environment         STRING      COMMENT 'prod | preprod | dev | test — primary environment for this domain',

    -- Audit
    registered_at       TIMESTAMP   COMMENT 'When domain was first registered',
    registered_by       STRING      COMMENT 'Who registered it',
    updated_at          TIMESTAMP   COMMENT 'Last update timestamp',
    notes               STRING      COMMENT 'Free text notes'

)
LOCATION 's3://your-zamboni-metadata-bucket/domain_registry/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY'
);

-- =============================================================================
-- Seed data — initial domains
-- Run after CREATE TABLE
-- =============================================================================

INSERT INTO zamboni_catalog.domain_registry VALUES
    ('ers',         'ERS',          'Enterprise Reporting System',          NULL, NULL, NULL, true,  7,  365, 60, 120, true, 'prod', NOW(), 'system', NOW(), 'Seeded on initial deploy'),
    ('finance',     'Finance',      'Finance and payments domain',          NULL, NULL, NULL, true,  30, 365, 60, 120, true, 'prod', NOW(), 'system', NOW(), 'Seeded on initial deploy'),
    ('financials',  'Financials',   'Financial reporting domain',           NULL, NULL, NULL, true,  30, 365, 60, 120, true, 'prod', NOW(), 'system', NOW(), 'Seeded on initial deploy'),
    ('membership',  'Membership',   'Membership and customer data domain',  NULL, NULL, NULL, true,  14, 365, 60, 120, true, 'prod', NOW(), 'system', NOW(), 'Seeded on initial deploy'),
    ('claims',      'Claims',       'Claims processing domain',             NULL, NULL, NULL, true,  90, 365, 60, 120, true, 'prod', NOW(), 'system', NOW(), 'Seeded on initial deploy'),
    ('travel',      'Travel',       'Travel and bookings domain',           NULL, NULL, NULL, true,  7,  365, 60, 120, true, 'prod', NOW(), 'system', NOW(), 'Seeded on initial deploy');
