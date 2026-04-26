"""
Zamboni — Base Engine
Abstract base class for all three engines.
Provides common structure: run_id generation, dry_run flag,
logging, and hooks for subclasses to implement.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone

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
        self.started_at = datetime.now(timezone.utc)
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
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        log.info(
            "engine.run_complete",
            engine=self.engine_name,
            run_id=self.run_id,
            elapsed_seconds=round(elapsed, 1),
            **{k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))},
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
                (datetime.now(timezone.utc) - self.started_at).total_seconds(), 1
            ),
            **extra,
        }
