-- =============================================================================
-- Control-M Job Registry
-- Catalog of Control-M jobs referenced by stream_registry's
-- controlm_pipeline_job/controlm_hk_job/dependent_on_controlm_job columns.
-- Not a foreign key -- a job removed here does not cascade into
-- stream_registry (see api/services/controlm_svc.py's Job List "Tables
-- Mapped" warning for the operator-facing consequence of that).
--
-- No prior Athena DDL existed for this table (only ever defined in
-- scripts/seed_local_db.py's local-mode fixture) -- this is the first
-- real Athena artifact for it, needed by scripts/control_plane_sync.py.
-- Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS zamboni_catalog.controlm_jobs (

    job_name              STRING    COMMENT 'Control-M job name, unique',
    job_type               STRING    COMMENT 'AWS service type for the Gate 1 upstream completion check',
    description            STRING    COMMENT 'Free text',
    domain                 STRING    COMMENT 'Owning domain, informational',
    environment             STRING    COMMENT 'prod | preprod | dev | test',
    expected_start_time    STRING    COMMENT 'HH:MM, informational scheduling hint',
    expected_duration_min  INT       COMMENT 'Informational scheduling hint',
    job_frequency           STRING    COMMENT 'daily | weekly | monthly | ad-hoc, informational',
    active                 BOOLEAN   COMMENT 'Soft-disable flag, informational only',
    registered_by           STRING    COMMENT 'Actor or auto:bulk_apply',
    created_at              TIMESTAMP,
    updated_at              TIMESTAMP

)
LOCATION 's3://your-zamboni-metadata-bucket/controlm_jobs/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
