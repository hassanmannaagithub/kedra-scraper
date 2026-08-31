from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

import kedra_scraper.scraper.pipelines as pipelines_module
from kedra_scraper.scraper.items import DocumentItem
from kedra_scraper.scraper.pipelines import DocumentPipeline
from kedra_scraper.utils.hashing import sha256_bytes


class Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


class DocumentService:
    def __init__(self):
        self.documents = []

    def content_exists(self, identifier, file_hash):
        return any(
            document.identifier == identifier and document.file_hash == file_hash
            for document in self.documents
        )

    def next_version(self, identifier):
        return 1 + sum(
            document.identifier == identifier for document in self.documents
        )

    def insert(self, document):
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


def test_html_comments_from_any_source_do_not_create_a_duplicate(monkeypatch):
    first_response = (
        b"<html><!-- generated\nrequest: 123 --><body>Decision</body></html>"
    )
    second_response = (
        b"<html><!-- generated\nrequest: 456 --><body>Decision</body></html>"
    )
    stable_content = b"<html><body>Decision</body></html>"
    document_service = DocumentService()
    object_store = ObjectStore()
    crawler = SimpleNamespace(
        spider=SimpleNamespace(
            documents=document_service,
            logger=SimpleNamespace(info=lambda *_args: None),
        ),
        stats=Stats(),
    )
    monkeypatch.setattr(pipelines_module, "ObjectStore", lambda: object_store)
    monkeypatch.setattr(
        pipelines_module,
        "get_settings",
        lambda: SimpleNamespace(landing_bucket="landing-zone"),
    )
    pipeline = DocumentPipeline.from_crawler(crawler)
    pipeline.open_spider()

    pipeline.process_item(build_html_item(first_response, source_id="another-source"))
    with pytest.raises(DropItem, match="unchanged content"):
        pipeline.process_item(
            build_html_item(second_response, source_id="another-source")
        )

    assert len(document_service.documents) == 1
    assert len(object_store.uploads) == 1
    assert object_store.uploads[0][2] == stable_content
    assert document_service.documents[0].file_hash == sha256_bytes(stable_content)
    assert crawler.stats.values == {
        "kedra/scraped": 1,
        "kedra/skipped_unchanged": 1,
    }
