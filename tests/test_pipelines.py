import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from scrapy.exceptions import DropItem

import kedra_scraper.scraper.pipelines as pipelines_module
from kedra_scraper.scraper.items import DocumentItem
from kedra_scraper.scraper.pipelines import DocumentPipeline
from kedra_scraper.services.document import DocumentService
from kedra_scraper.utils.hashing import sha256_bytes


class Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


class Collection:
    def __init__(self):
        self.documents = []

    async def find_one(self, query, sort=None):
        await asyncio.sleep(0)
        matches = [
            document for document in self.documents
            if all(document[key] == value for key, value in query.items())
        ]
        if sort:
            matches.sort(key=lambda document: document["version"], reverse=True)
        return matches[0] if matches else None

    async def insert_one(self, document):
        await asyncio.sleep(0)
        self.documents.append(document)


class ObjectStore:
    def __init__(self):
        self.uploads = []

    def ensure_buckets(self):
        pass

    def put_bytes(self, bucket, key, data, content_type):
        self.uploads.append((bucket, key, data, content_type))
        return f"{bucket}/{key}"


def build_html_item(content, source_id="wrc"):
    return DocumentItem(
        identifier="ADJ-0001",
        title="Decision",
        description="A v B",
        published_date=date(2026, 1, 30),
        source_id=source_id,
        section_id="wrc_adjudication",
        partition_date=date(2026, 1, 30),
        source_url="https://example.com/decision.html",
        doc_link="https://example.com/decision.html",
        doc_type="html",
        status="stored",
        run_id="run-1",
        scraped_at=datetime.now(timezone.utc),
        content=content,
    )


@pytest.fixture
def pipeline_state(monkeypatch):
    collection = Collection()
    object_store = ObjectStore()
    crawler = SimpleNamespace(
        spider=SimpleNamespace(
            dbs=SimpleNamespace(documents=MagicMock()),
            logger=SimpleNamespace(
                info=lambda *_args: None, warning=lambda *_args: None,
            ),
        ),
        stats=Stats(),
    )
    client = MagicMock()
    client.__getitem__.return_value = SimpleNamespace(documents=collection)
    client.close = AsyncMock()
    monkeypatch.setattr(pipelines_module, "get_async_mongo_client", lambda: client)
    monkeypatch.setattr(pipelines_module, "ObjectStore", lambda: object_store)
    monkeypatch.setattr(
        pipelines_module,
        "get_settings",
        lambda: SimpleNamespace(landing_bucket="landing-zone", landing_db="landing"),
    )
    pipeline = DocumentPipeline.from_crawler(crawler)
    return SimpleNamespace(
        pipeline=pipeline, collection=collection, object_store=object_store,
        stats=crawler.stats, client=client,
    )


def test_html_comments_from_any_source_do_not_create_a_duplicate(pipeline_state):
    first_response = (
        b"<html><!-- generated\nrequest: 123 --><body>Decision</body></html>"
    )
    second_response = (
        b"<html><!-- generated\nrequest: 456 --><body>Decision</body></html>"
    )
    stable_content = b"<html><body>Decision</body></html>"
    state = pipeline_state
    pipeline = state.pipeline

    async def run():
        pipeline.open_spider()
        try:
            await pipeline.process_item(
                build_html_item(first_response, source_id="another-source")
            )
            with pytest.raises(DropItem, match="unchanged content"):
                await pipeline.process_item(
                    build_html_item(second_response, source_id="another-source")
                )
        finally:
            await pipeline.close_spider()

    asyncio.run(run())
    assert len(state.collection.documents) == 1
    assert len(state.object_store.uploads) == 1
    assert state.object_store.uploads[0][2] == stable_content
    document = state.collection.documents[0]
    assert document["file_hash"] == sha256_bytes(stable_content)
    assert document["partition_date"] == datetime(2026, 1, 30, tzinfo=timezone.utc)
    assert type(pipeline.document_service) is DocumentService
    pipeline.document_service.databases.documents.find_one.assert_not_called()
    pipeline.document_service.databases.documents.insert_one.assert_not_called()
    assert "content" not in document
    state.client.close.assert_awaited_once()
    assert state.stats.values == {
        "kedra/scraped": 1,
        "kedra/skipped_unchanged": 1,
    }


@pytest.mark.parametrize("same_content", [True, False])
def test_concurrent_items_preserve_deduplication_and_versions(pipeline_state, same_content):
    state = pipeline_state
    first = build_html_item(b"first")
    second = build_html_item(b"first" if same_content else b"second")

    async def run():
        state.pipeline.open_spider()
        try:
            return await asyncio.gather(
                state.pipeline.process_item(first),
                state.pipeline.process_item(second),
                return_exceptions=True,
            )
        finally:
            await state.pipeline.close_spider()

    results = asyncio.run(run())
    assert results[0] is first
    if same_content:
        assert isinstance(results[1], DropItem)
        assert len(state.object_store.uploads) == 1
    else:
        assert results[1] is second
        assert [doc["version"] for doc in state.collection.documents] == [1, 2]
        assert state.object_store.uploads[0][1] != state.object_store.uploads[1][1]


def test_other_items_run_while_mongo_insert_waits(pipeline_state, monkeypatch):
    state = pipeline_state
    original_insert = state.collection.insert_one
    first = build_html_item(b"first")
    second = build_html_item(b"second")
    second.identifier = "ADJ-0002"

    async def run():
        insert_started = asyncio.Event()
        release_insert = asyncio.Event()

        async def insert_one(document):
            assert any(
                document["file_path"] == f"{bucket}/{key}"
                for bucket, key, _data, _content_type in state.object_store.uploads
            )
            if document["identifier"] == first.identifier:
                insert_started.set()
                await asyncio.wait_for(release_insert.wait(), timeout=5)
            await original_insert(document)

        monkeypatch.setattr(state.collection, "insert_one", insert_one)
        state.pipeline.open_spider()
        pending = asyncio.create_task(state.pipeline.process_item(first))
        try:
            await asyncio.wait_for(insert_started.wait(), timeout=2)
            assert state.collection.documents == []
            assert not pending.done()
            assert await asyncio.wait_for(state.pipeline.process_item(second), timeout=2) is second
            assert [doc["identifier"] for doc in state.collection.documents] == [second.identifier]
            release_insert.set()
            assert await asyncio.wait_for(pending, timeout=2) is first
            assert state.collection.documents[-1]["file_path"] == first.file_path
        finally:
            release_insert.set()
            await asyncio.gather(pending, return_exceptions=True)
            await state.pipeline.close_spider()

    asyncio.run(run())


def test_upload_failure_does_not_insert_metadata_and_allows_retry(pipeline_state, monkeypatch):
    state = pipeline_state
    original_put = state.object_store.put_bytes

    def fail_upload(*args):
        raise OSError("upload failed")

    async def run():
        state.pipeline.open_spider()
        try:
            monkeypatch.setattr(state.object_store, "put_bytes", fail_upload)
            with pytest.raises(OSError, match="upload failed"):
                await state.pipeline.process_item(build_html_item(b"first"))
            assert state.collection.documents == []
            assert state.stats.values == {}
            monkeypatch.setattr(state.object_store, "put_bytes", original_put)
            item = await state.pipeline.process_item(build_html_item(b"first"))
            assert item.version == 1
        finally:
            await state.pipeline.close_spider()

    asyncio.run(run())


def test_failed_item_skips_upload_and_does_not_suppress_success(pipeline_state):
    state = pipeline_state
    failed_item = build_html_item(None)
    failed_item.status = "failed"
    failed_item.error = {"message": "download failed"}

    async def run():
        state.pipeline.open_spider()
        try:
            assert await state.pipeline.process_item(failed_item) is failed_item
            assert state.object_store.uploads == []
            assert state.collection.documents[0]["file_path"] is None
            assert state.stats.values == {"kedra/failed": 1}
            stored_item = await state.pipeline.process_item(build_html_item(b"first"))
            assert stored_item.version == 2
        finally:
            await state.pipeline.close_spider()

    asyncio.run(run())


def test_document_service_preserves_synchronous_spider_and_transform_reads():
    collection = MagicMock()
    collection.find_one.return_value = {"etag": "saved-etag"}
    stored_documents = iter([{"identifier": "ADJ-0001", "version": 1}])
    collection.find.return_value.sort.return_value = stored_documents
    service = DocumentService(SimpleNamespace(documents=collection))

    assert service.get_latest_etag("ADJ-0001", "section", "https://example.com/1") == "saved-etag"
    assert list(service.get_stored_documents_by_partition_range(
        date(2026, 1, 1), date(2026, 1, 31), "section",
    )) == [{"identifier": "ADJ-0001", "version": 1}]
    collection.find_one.assert_called_once_with(
        {
            "identifier": "ADJ-0001", "section_id": "section",
            "doc_link": "https://example.com/1", "status": "stored",
        },
        sort=[("version", -1)],
    )
