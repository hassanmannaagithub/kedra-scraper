import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import kedra_scraper.scraper.runner as runner_module
from kedra_scraper.services.run import RunService
from kedra_scraper.services.transformed_document import TransformedDocumentService
from kedra_scraper.utils.db import Databases, ensure_indexes


def make_databases():
    client = SimpleNamespace(close=AsyncMock())
    return Databases(
        landing=SimpleNamespace(
            client=client, documents=SimpleNamespace(create_index=AsyncMock()),
        ),
        transformed=SimpleNamespace(documents=SimpleNamespace(
            create_index=AsyncMock(), find_one=AsyncMock(), insert_one=AsyncMock(),
        )),
        meta=SimpleNamespace(run_summaries=SimpleNamespace(
            create_index=AsyncMock(), find_one=AsyncMock(), update_one=AsyncMock(),
        )),
    )


@pytest.mark.parametrize("fail", [False, True])
def test_index_setup_awaits_operations_and_closes_client(monkeypatch, fail):
    databases = make_databases()
    if fail:
        databases.transformed_documents.create_index.side_effect = OSError("unavailable")
    monkeypatch.setattr(runner_module, "get_databases", lambda: databases)

    if fail:
        with pytest.raises(OSError, match="unavailable"):
            asyncio.run(runner_module._prepare_database())
    else:
        asyncio.run(runner_module._prepare_database())
        databases.run_summaries.create_index.assert_awaited_once_with(
            [("run_id", 1)], unique=True,
        )
    assert databases.documents.create_index.await_count == 3
    databases.transformed_documents.create_index.assert_awaited_once_with(
        [("source_file_path", 1)], unique=True,
    )
    databases.landing.client.close.assert_awaited_once()


@pytest.mark.parametrize("stored_document, expected", [(None, False), ({"version": 1}, True)])
def test_transformed_document_lookup_awaits_before_testing_existence(stored_document, expected):
    databases = make_databases()
    databases.transformed_documents.find_one.return_value = stored_document
    service = TransformedDocumentService(databases)

    assert asyncio.run(service.exists_for_source_file("landing/one.pdf")) is expected
    databases.transformed_documents.find_one.assert_awaited_once_with(
        {"source_file_path": "landing/one.pdf"},
    )


def test_run_summary_awaits_atomic_update_and_read():
    databases = make_databases()
    databases.run_summaries.find_one.return_value = {"run_id": "run-1"}
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service = RunService(databases)

    async def run():
        await service.record_partition(
            run_id="run-1", source_id="wrc", section_id="section",
            window=(date(2026, 1, 1), date(2026, 1, 31)),
            document_counts_by_status={"found": 2, "scraped": 1},
            finish_reason="finished", fatal=None,
            started_at=started_at, finished_at=started_at,
        )
        assert await service.get_run_by_id("run-1") == {"run_id": "run-1"}

    asyncio.run(run())
    databases.run_summaries.update_one.assert_awaited_once()
    args, kwargs = databases.run_summaries.update_one.await_args
    assert args[0] == {"run_id": "run-1"}
    assert args[1]["$inc"] == {"totals.found": 2, "totals.scraped": 1}
    assert args[1]["$push"]["partitions"]["partition_date"] == started_at
    assert kwargs == {"upsert": True}
    databases.run_summaries.find_one.assert_awaited_once_with({"run_id": "run-1"}, {"_id": 0})


@pytest.mark.parametrize("fail", [False, True])
def test_partition_summary_owns_and_closes_client_after_crawl(monkeypatch, fail):
    databases = make_databases()
    events = []

    def run_crawl(*args):
        events.append("crawl")
        return {"finish_reason": "finished", "kedra/found": 1, "kedra/scraped": 1}

    async def update(*args, **kwargs):
        events.append("summary")
        if fail:
            raise OSError("summary failed")

    databases.run_summaries.update_one.side_effect = update
    monkeypatch.setattr(runner_module, "get_databases", lambda: databases)
    monkeypatch.setattr(runner_module, "_run_crawl", run_crawl)
    monkeypatch.setattr(runner_module, "_resolve_config", lambda _: (
        SimpleNamespace(section_id="section"), SimpleNamespace(source_id="wrc"),
    ))
    args = ("section", date(2026, 1, 1), date(2026, 1, 31), "run-1")
    if fail:
        with pytest.raises(OSError, match="summary failed"):
            runner_module.run_partition(*args)
    else:
        result = runner_module.run_partition(*args)
        assert result["document_counts_by_status"]["scraped"] == 1
    assert events == ["crawl", "summary"]
    databases.landing.client.close.assert_awaited_once()
