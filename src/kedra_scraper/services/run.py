"""Run slice: the write contract for ``meta.run_summaries`` and the service
that owns it (partition recording, plus reads over it)."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kedra_scraper.utils.db import bson_safe, get_databases


class DocumentCounters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: int = 0
    scraped: int = 0
    failed: int = 0
    skipped_unchanged: int = 0


class PartitionSummary(DocumentCounters):
    section_id: str
    partition_date: date
    window_start: date
    window_end: date
    finish_reason: str
    fatal: Optional[str] = None
    started_at: datetime
    finished_at: datetime
    duration_seconds: float


class RunSummary(BaseModel):
    """One row of ``meta.run_summaries``. Partition entries are appended (and
    totals $inc'd) atomically so parallel mapped tasks never race."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    source_id: str
    partitions: list[PartitionSummary] = Field(default_factory=list)
    totals: DocumentCounters = Field(default_factory=DocumentCounters)
    started_at: datetime
    finished_at: Optional[datetime] = None


class RunService:
    """The only writer of ``meta.run_summaries``."""

    def __init__(self):
        self.databases = get_databases()

    def get_run_by_id(self, run_id: str) -> Optional[dict]:
        return self.databases.run_summaries.find_one(
            {"run_id": run_id},
            {"_id": 0},
        )

    def record_partition(self, *, run_id: str, source_id: str, section_id: str,
                         window: tuple[date, date],
                         document_counts_by_status: dict[str, int],
                         finish_reason: str,
                         fatal: Optional[str], started_at: datetime,
                         finished_at: datetime) -> None:
        """Atomic $push/$inc so parallel mapped tasks never race."""
        partition_summary = PartitionSummary(
            section_id=section_id,
            partition_date=window[0],
            window_start=window[0],
            window_end=window[1],
            finish_reason=finish_reason,
            fatal=fatal,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=(finished_at - started_at).total_seconds(),
            **document_counts_by_status,
        )
        run_total_increments_by_field = {}
        for metric, count in document_counts_by_status.items():
            run_total_increments_by_field[f"totals.{metric}"] = count
        self.databases.run_summaries.update_one(
            {"run_id": run_id},
            {
                "$setOnInsert": {"run_id": run_id, "source_id": source_id,
                                 "started_at": started_at},
                "$push": {
                    "partitions": bson_safe(partition_summary.model_dump())
                },
                "$inc": run_total_increments_by_field,
                "$set": {"finished_at": finished_at},
            },
            upsert=True,
        )
