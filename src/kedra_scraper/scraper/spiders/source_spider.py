"""The only spider. Drives the loop for one (section, partition window)
through the config-driven ``SourceParser``, and contains zero selectors
and zero site names.

Failure containment: a bad record yields a ``status: failed``
item and the crawl continues; a listing-level failure (zero-match selector,
listing fetch dead after retries) kills the partition, because a partition
that silently scrapes nothing is worse than one that fails loudly.
"""

from datetime import date, datetime, timezone
from typing import Optional

import scrapy
from scrapy import signals
from scrapy.exceptions import CloseSpider
from scrapy.spidermiddlewares.httperror import HttpError

from kedra_scraper.services.config import ConfigService
from kedra_scraper.services.document import DocumentService
from kedra_scraper.utils.db import get_databases
from kedra_scraper.utils.hashing import sha256_bytes
from kedra_scraper.scraper.items import DocumentItem
from kedra_scraper.scraper.parser import (DocRef, ListingRecord, SourceParser,
                                          SelectorMatchError)


class SourceSpider(scrapy.Spider):
    name = "source"

    def __init__(self, section: str, run_id: str, start: str, end: str, **kwargs):
        super().__init__(**kwargs)
        self.section_id = section
        self.run_id = run_id
        self.window_start = date.fromisoformat(start)
        self.window_end = date.fromisoformat(end)
        self.partition_date = self.window_start

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(
            spider._on_spider_closed,
            signal=signals.spider_closed,
        )
        spider._initialize_dependencies()
        return spider

    def _initialize_dependencies(self):
        """Load the config once, before any request goes out —
        malformed config fails here, not on page 40."""
        self.dbs = get_databases()
        self.documents = DocumentService(self.dbs)
        config_service = ConfigService()
        section_config = config_service.get_section(self.section_id)
        source_config = config_service.get_source(section_config.source_id)
        self.source_config = source_config
        self.parser = SourceParser(source_config, section_config)

    async def start(self):
        url = self.parser.build_listing_url(
            self.window_start,
            self.window_end,
            page_number=1,
        )
        yield scrapy.Request(
            url,
            callback=self.handle_listing,
            errback=self.errback_listing,
        )

    def handle_listing(self, response):
        try:
            parsed_listing = self.parser.parse_listing_page(response)
        except SelectorMatchError as exc:
            self.crawler.stats.set_value("kedra/fatal", f"listing: {exc}")
            raise CloseSpider("zero_match")

        listing_records = parsed_listing.records
        row_errors = parsed_listing.errors
        proxy_host = response.meta.get("proxy_host")

        self.crawler.stats.inc_value(
            "kedra/found",
            len(listing_records) + len(row_errors),
        )

        for row_error in row_errors:
            identifier = f"unknown:{sha256_bytes(row_error.snippet.encode())[:16]}"
            yield self._failed_item(
                identifier,
                "listing_record",
                row_error.reason,
                response.url,
                None,
                proxy=proxy_host,
            )

        for listing_record in listing_records:
            try:
                request = scrapy.Request(
                    listing_record.detail_link,
                    callback=self.handle_detail,
                    errback=self.errback_record,
                    headers=self._get_condition_headers(
                        listing_record.identifier,
                        listing_record.detail_link,
                    ),
                    meta={
                        "listing_record": listing_record,
                        "handle_httpstatus_list": [304],
                    },
                )
            except Exception as exc:
                yield self._failed_item(
                    listing_record.identifier,
                    "listing_record",
                    repr(exc),
                    listing_record.detail_link,
                    listing_record,
                    proxy=proxy_host,
                )
                continue

            yield request

        next_page_url = self.parser.get_next_page_url(response)

        if next_page_url is not None:
            yield scrapy.Request(
                next_page_url,
                callback=self.handle_listing,
                errback=self.errback_listing,
            )

    def handle_detail(self, response):
        if response.status == 304:
            self.crawler.stats.inc_value("kedra/skipped_unchanged")
            return

        listing_record: ListingRecord = response.meta["listing_record"]
        try:
            docref = self.parser.parse_detail_page(response)
        except SelectorMatchError as exc:
            yield self._failed_item(
                listing_record.identifier,
                "detail",
                str(exc),
                response.url,
                listing_record,
                proxy=response.meta.get("proxy_host"),
            )
            return

        is_detail_page_document = docref.url == response.url

        if is_detail_page_document:
            yield self._build_document_item(listing_record, docref, response)
            return

        yield scrapy.Request(
            docref.url,
            callback=self.handle_document,
            errback=self.errback_record,
            headers=self._get_condition_headers(
                listing_record.identifier,
                docref.url,
            ),
            meta={
                "listing_record": listing_record,
                "docref": docref,
                "handle_httpstatus_list": [304],
            },
        )

    def handle_document(self, response):
        if response.status == 304:
            self.crawler.stats.inc_value("kedra/skipped_unchanged")
            return

        yield self._build_document_item(
            response.meta["listing_record"],
            response.meta["docref"],
            response,
        )

    def _build_document_item(
        self,
        listing_record: ListingRecord,
        docref: DocRef,
        response,
    ) -> DocumentItem:
        content_type = (response.headers.get("Content-Type") or b"").decode("latin-1")
        doc_type = docref.doc_type
        if not doc_type:
            doc_type = self.parser.get_doc_type(docref.url, content_type)
        if not doc_type:
            doc_type = "bin"
        etag_header = response.headers.get("ETag")
        etag = None
        if etag_header:
            etag = etag_header.decode("latin-1")
        return DocumentItem(
            identifier=listing_record.identifier,
            title=listing_record.title,
            description=listing_record.description,
            published_date=listing_record.published_date,
            source_id=self.source_config.source_id,
            section_id=self.section_id,
            partition_date=self.partition_date,
            source_url=listing_record.detail_link,
            doc_link=docref.url,
            doc_type=doc_type,
            status="stored",
            run_id=self.run_id,
            scraped_at=datetime.now(timezone.utc),
            content=bytes(response.body),
            etag=etag,
            proxy=response.meta.get("proxy_host"),
        )

    def _get_condition_headers(
        self,
        identifier: str,
        document_url: str,
    ) -> dict[str, str]:
        etag = self.documents.get_latest_etag(
            identifier,
            self.section_id,
            document_url,
        )
        if not etag:
            return {}
        return {"If-None-Match": etag}

    def _failed_item(
        self,
        identifier: str,
        stage: str,
        reason: str,
        url: str,
        listing_record: Optional[ListingRecord],
        status_code: Optional[int] = None,
        proxy: Optional[str] = None,
    ) -> DocumentItem:
        error = {"stage": stage, "reason": reason, "url": url}
        if status_code is not None:
            error["status_code"] = status_code
        if listing_record is not None:
            title = listing_record.title
            description = listing_record.description
            published_date = listing_record.published_date
            source_url = listing_record.detail_link
        else:
            title = ""
            description = ""
            published_date = None
            source_url = url
        return DocumentItem(
            identifier=identifier,
            title=title,
            description=description,
            published_date=published_date,
            source_id=self.source_config.source_id,
            section_id=self.section_id,
            partition_date=self.partition_date,
            source_url=source_url,
            doc_link=url,
            doc_type="unknown",
            status="failed",
            run_id=self.run_id,
            scraped_at=datetime.now(timezone.utc),
            error=error,
            proxy=proxy,
        )

    def errback_record(self, failure):
        if failure.check(HttpError):
            status_code = failure.value.response.status
        else:
            status_code = None
        request = failure.request
        listing_record = request.meta.get("listing_record")
        if listing_record is not None:
            identifier = listing_record.identifier
        else:
            identifier = f"unknown:{request.url}"
        yield self._failed_item(
            identifier,
            "download",
            f"{failure.type.__name__}: {failure.getErrorMessage()}",
            request.url,
            listing_record,
            status_code,
            proxy=request.meta.get("proxy_host"),
        )

    def errback_listing(self, failure):
        message = f"listing fetch failed: {failure.request.url} — {failure.getErrorMessage()}"
        self.crawler.stats.set_value("kedra/fatal", message)
        self.logger.error(message)
        raise CloseSpider("listing_failed")

    def _on_spider_closed(self, reason):
        self.dbs.landing.client.close()
