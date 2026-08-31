"""Date-range → partition list. Partitions are fixed-size day windows:
``partition_days`` per section because volume across the four
bodies is heavily skewed — short windows for the busy body, long windows for
the quiet ones."""

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict


class Partition(BaseModel):
    """One crawl unit: an inclusive date window for one section.
    ``partition_date`` (the window start) identifies the partition on
    documents and run summaries — a mid-window backfill and a full-window
    run map to the same partition identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str
    partition_date: date
    start: date
    end: date


def build_partitions(
    section_id: str, start: date, end: date, days: int = 30
) -> list[Partition]:
    if end < start:
        raise ValueError(f"end {end} before start {start}")
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")

    partition_windows = []
    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days=days - 1), end)
        partition_windows.append(
            Partition(
                section_id=section_id,
                partition_date=window_start,
                start=window_start,
                end=window_end,
            )
        )
        window_start = window_end + timedelta(days=1)
    return partition_windows
