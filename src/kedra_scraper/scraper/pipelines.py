"""The item pipeline: dedup -> object storage -> Mongo -> document counts.

One class, so the order of operations is visible in one place. The object
store write happens before the Mongo insert, never the reverse — a Mongo row
must never point at a file that does not exist. Failed items carry no
content: they skip hashing and upload and land in Mongo as ``status:
failed`` rows.
"""

import re

from scrapy.exceptions import DropItem

from kedra_scraper.config import get_settings
from kedra_scraper.utils.document_formats import (
    CONTENT_TYPE_BY_DOCUMENT_TYPE,
    EXTENSION_BY_DOCUMENT_TYPE,
)
from kedra_scraper.scraper.items import DocumentItem
from kedra_scraper.services.document import Document
from kedra_scraper.utils.hashing import sha256_bytes
from kedra_scraper.utils.objects import ObjectStore

_HTML_COMMENT = re.compile(rb"<!--.*?-->", re.DOTALL)


def _stable_content(item: DocumentItem) -> bytes:
    if item.content is None:
        raise ValueError(f"stored item {item.identifier!r} has no content")
    content = item.content
    if item.doc_type == "html":
        return _HTML_COMMENT.sub(b"", content)
    return content


class DocumentPipeline:

    def __init__(self, crawler):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def open_spider(self):
        spider = self.crawler.spider
        self.document_service = spider.documents
        self.logger = spider.logger
        self.stats = self.crawler.stats
        self.object_store = ObjectStore()
        self.object_store.ensure_buckets()
        self.landing_bucket = get_settings().landing_bucket

    def process_item(self, item: DocumentItem):
        if item.status == "failed":
            item.version = self.document_service.next_version(item.identifier)
            self._insert_document_metadata(item)
            self.stats.inc_value("kedra/failed")
            self.logger.warning("failed %s: %s", item.identifier, item.error)
            return item

        item.content = _stable_content(item)
        item.file_hash = sha256_bytes(item.content)
        if self.document_service.content_exists(item.identifier, item.file_hash):
            self.stats.inc_value("kedra/skipped_unchanged")
            raise DropItem(f"unchanged content: {item.identifier}")

        item.version = self.document_service.next_version(item.identifier)
        self._upload_document_content(item)
        self._insert_document_metadata(item)
        self.stats.inc_value("kedra/scraped")
        self.logger.info(
            "stored %s v%s (%s) -> %s",
            item.identifier, item.version, item.doc_type, item.file_path,
        )
        return item

    def _upload_document_content(self, item: DocumentItem) -> None:
        storage_safe_identifier = item.identifier.replace("/", "_").replace(" ", "_")
        extension = EXTENSION_BY_DOCUMENT_TYPE.get(item.doc_type, "bin")
        object_key = (
            f"{item.section_id}/{item.partition_date.isoformat()}/"
            f"{storage_safe_identifier}/v{item.version}.{extension}"
        )
        content_type = CONTENT_TYPE_BY_DOCUMENT_TYPE.get(
            item.doc_type,
            "application/octet-stream",
        )
        self.object_store.put_bytes(
            self.landing_bucket,
            object_key,
            item.content,
            content_type,
        )
        item.file_path = f"{self.landing_bucket}/{object_key}"

    def _insert_document_metadata(self, item: DocumentItem) -> None:
        document_fields = {}
        for field_name in Document.model_fields:
            document_fields[field_name] = getattr(item, field_name)
        self.document_service.insert(Document(**document_fields))
