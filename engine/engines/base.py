"""
Zamboni — Base Engine
Abstract base class for all three engines.
Provides common structure: run_id generation, dry_run flag,
logging, and hooks for subclasses to implement.
"""
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from engine.core.execution_log import new_run_id
from engine.utils.logger import get_logger

log = get_logger(__name__)


class BaseEngine(ABC):
    """
    Abstract base engine. All three engines inherit from this.

    Subclasses must implement:
        - run() — main execution entry point
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run  = dry_run
        self.run_id   = new_run_id()
        self.started_at = datetime.now(UTC)
        self.engine_name = self.__class__.__name__

        log.info(
            "engine.init",
            engine=self.engine_name,
            run_id=self.run_id,
            dry_run=self.dry_run,
        )

    @abstractmethod
    def run(self, **kwargs) -> dict:
        """
        Execute the engine.

        Returns:
            dict with run summary — tables processed, successes,
            failures, skips, and any relevant metrics.
        """

    def _log_start(self, scope: str, **kwargs) -> None:
        log.info(
            "engine.run_start",
            engine=self.engine_name,
            run_id=self.run_id,
            dry_run=self.dry_run,
            scope=scope,
            **kwargs,
        )

    def _log_complete(self, result: dict) -> None:
        """
        Real bug fixed here (found while testing an unrelated lifecycle_engine
        change, 2026-07-09): summary_dict() always includes "engine",
        "run_id", and "elapsed_seconds" keys in its returned dict, and this
        call also passes those same three as explicit kwargs -- Python
        raises TypeError ("got multiple values for keyword argument") for
        **any** call shaped like
        f(engine=x, **{"engine": y}), unconditionally, regardless of
        structlog's own internals. This meant every top-level HKEngine.run(),
        ArchivalEngine.run(), and LifecycleEngine.run()/run_scan()/
        run_cleanup() call crashed with an unhandled TypeError on this line,
        right after all real maintenance work for the run had already
        completed -- the actual engine.scripts.run_hk/run_archival/
        run_cleanup/run_lifecycle_cycle/run_lifecycle_scan.py entry points
        (the real EventBridge/Control-M-triggered jobs) never returned a
        result or exited cleanly. Reproduced directly against real
        HKEngine/LifecycleEngine instances with zero mocking before fixing.
        """
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        _already_explicit = {"engine", "run_id", "elapsed_seconds"}
        log.info(
            "engine.run_complete",
            engine=self.engine_name,
            run_id=self.run_id,
            elapsed_seconds=round(elapsed, 1),
            **{
                k: v for k, v in result.items()
                if k not in _already_explicit and isinstance(v, (int, float, str, bool))
            },
        )

    def _log_table_skip(self, table_fqn: str, reason: str) -> None:
        log.info(
            "engine.table_skip",
            engine=self.engine_name,
            run_id=self.run_id,
            table_fqn=table_fqn,
            reason=reason,
        )

    def _log_table_error(self, table_fqn: str, error: Exception) -> None:
        log.error(
            "engine.table_error",
            engine=self.engine_name,
            run_id=self.run_id,
            table_fqn=table_fqn,
            error=str(error),
        )

    def summary_dict(
        self,
        tables_processed: int,
        succeeded: int,
        failed: int,
        skipped: int,
        **extra,
    ) -> dict:
        """Build a standardised run summary dict."""
        return {
            "engine":            self.engine_name,
            "run_id":            self.run_id,
            "dry_run":           self.dry_run,
            "tables_processed":  tables_processed,
            "succeeded":         succeeded,
            "failed":            failed,
            "skipped":           skipped,
            "elapsed_seconds":   round(
                (datetime.now(UTC) - self.started_at).total_seconds(), 1
            ),
            **extra,
        }
