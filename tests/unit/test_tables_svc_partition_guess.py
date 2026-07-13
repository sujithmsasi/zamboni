"""
api/services/tables_svc.py::_guess_partition_column_for_registration()

register_table() previously never passed partition_column to apply_template()
at all, so every table registered through Browse & Register silently got
apply_template()'s hardcoded "partition_date" default regardless of the
table's real schema. This guesses a real column from the table's own Glue
StorageDescriptor.Columns (reusing get_tables()'s cached result -- no new
Glue call), falling back to "partition_date" only when it can't.
"""
from __future__ import annotations

from api.services.tables_svc import _guess_partition_column_for_registration


def _glue_table(name: str, columns: list[dict]) -> dict:
    return {
        "Name": name,
        "Parameters": {"table_type": "ICEBERG"},
        "StorageDescriptor": {"Columns": columns},
    }


def test_guesses_real_partition_column_from_cached_glue_schema(monkeypatch):
    monkeypatch.setattr(
        "api.services.tables_svc.get_tables",
        lambda database: [_glue_table("fin_claims", [
            {"Name": "claim_id", "Type": "string"},
            {"Name": "transaction_date", "Type": "date"},
        ])],
    )
    req = {"table_fqn": "glue_catalog.finance_db.fin_claims", "table_format": "iceberg"}
    assert _guess_partition_column_for_registration(req) == "transaction_date"


def test_falls_back_to_partition_date_when_table_not_found_in_cache(monkeypatch):
    monkeypatch.setattr("api.services.tables_svc.get_tables", lambda database: [])
    req = {"table_fqn": "glue_catalog.finance_db.fin_claims", "table_format": "iceberg"}
    assert _guess_partition_column_for_registration(req) == "partition_date"


def test_falls_back_to_partition_date_when_no_date_column_exists(monkeypatch):
    monkeypatch.setattr(
        "api.services.tables_svc.get_tables",
        lambda database: [_glue_table("fin_claims", [{"Name": "claim_id", "Type": "string"}])],
    )
    req = {"table_fqn": "glue_catalog.finance_db.fin_claims", "table_format": "iceberg"}
    assert _guess_partition_column_for_registration(req) == "partition_date"


def test_skips_the_guess_entirely_for_non_iceberg_tables(monkeypatch):
    """Must not even call get_tables() for a hive table -- there's no Iceberg
    partition-spec concept to guess for those."""
    def _boom(database):
        raise AssertionError("get_tables() should not be called for a non-iceberg table")

    monkeypatch.setattr("api.services.tables_svc.get_tables", _boom)
    req = {"table_fqn": "glue_catalog.finance_db.fin_legacy", "table_format": "hive"}
    assert _guess_partition_column_for_registration(req) == "partition_date"


def test_falls_back_to_partition_date_on_any_error(monkeypatch):
    monkeypatch.setattr(
        "api.services.tables_svc.get_tables",
        lambda database: (_ for _ in ()).throw(RuntimeError("Glue unavailable")),
    )
    req = {"table_fqn": "glue_catalog.finance_db.fin_claims", "table_format": "iceberg"}
    assert _guess_partition_column_for_registration(req) == "partition_date"
