from datetime import date
from types import SimpleNamespace

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy import signals
from scrapy.spidermiddlewares.httperror import HttpError
from pydispatch.robustapply import robustApply

from kedra_scraper.scraper.parser import DocRef, ListingPage, ListingRecord
from kedra_scraper.scraper.spiders.source_spider import SourceSpider


class Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


class ClosableClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_spider_closes_when_scrapy_supplies_reason_as_a_keyword():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    client = ClosableClient()
    spider.dbs = SimpleNamespace(
        landing=SimpleNamespace(client=client),
    )

    robustApply(
        spider._on_spider_closed,
        signal=signals.spider_closed,
        sender=spider,
        spider=spider,
        reason="finished",
    )

    assert client.closed is True


def test_failed_download_captures_url_and_http_status():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.source_config = SimpleNamespace(source_id="test-source")
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    request = Request(
        listing_record.detail_link,
        meta={"listing_record": listing_record},
    )
    failure = SimpleNamespace(
        check=lambda error_type: error_type is HttpError,
        value=SimpleNamespace(response=SimpleNamespace(status=503)),
        request=request,
        type=HttpError,
        getErrorMessage=lambda: "Service Unavailable",
    )

    failed_item = list(spider.errback_record(failure))[0]

    assert failed_item.status == "failed"
    assert failed_item.identifier == "decision-1"
    assert failed_item.error == {
        "stage": "download",
        "reason": "HttpError: Service Unavailable",
        "url": "https://example.com/decision-1",
        "status_code": 503,
    }


def test_listing_record_without_etag_is_requested_normally():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.crawler = SimpleNamespace(stats=Stats())
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    spider.parser = SimpleNamespace(
        parse_listing_page=lambda _html, _url: ListingPage(records=[listing_record]),
        get_next_page_url=lambda _html, _url: None,
    )
    spider.documents = SimpleNamespace(
        get_latest_etag=lambda _identifier, _section_id, _url: None
    )
    response = HtmlResponse(
        url="https://example.com/listing",
        request=Request("https://example.com/listing"),
        body=b"<html></html>",
        encoding="utf-8",
    )

    yielded = list(spider.handle_listing(response))

    assert len(yielded) == 1
    detail_request = yielded[0]
    assert detail_request.url == listing_record.detail_link
    assert detail_request.callback == spider.handle_detail
    assert detail_request.dont_filter is False
    assert detail_request.meta["listing_record"] == listing_record
    assert spider.crawler.stats.values == {"kedra/found": 1}


def test_next_listing_page_uses_the_selector_href():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.crawler = SimpleNamespace(stats=Stats())
    spider.parser = SimpleNamespace(
        parse_listing_page=lambda _html, _url: ListingPage(records=[]),
        get_next_page_url=lambda _html, _url: (
            "https://example.com/listing?pageNumber=2"
        ),
    )
    response = HtmlResponse(
        url="https://example.com/listing?pageNumber=1",
        request=Request("https://example.com/listing?pageNumber=1"),
        body=b"<html></html>",
        encoding="utf-8",
    )

    yielded = list(spider.handle_listing(response))

    assert len(yielded) == 1
    next_page_request = yielded[0]
    assert next_page_request.url == "https://example.com/listing?pageNumber=2"


def test_document_request_uses_normal_url_filtering():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    spider.parser = SimpleNamespace(
        parse_detail_page=lambda _html, _url: DocRef(
            url="https://example.com/decision-1.pdf",
            doc_type="pdf",
        )
    )
    spider.documents = SimpleNamespace(
        get_latest_etag=lambda _identifier, _section_id, _url: "pdf-etag"
    )
    response = HtmlResponse(
        url=listing_record.detail_link,
        request=Request(
            listing_record.detail_link,
            meta={"listing_record": listing_record},
        ),
        body=b"<html></html>",
        encoding="utf-8",
    )

    yielded = list(spider.handle_detail(response))

    assert len(yielded) == 1
    document_request = yielded[0]
    assert document_request.url == "https://example.com/decision-1.pdf"
    assert document_request.dont_filter is False
    assert document_request.headers["If-None-Match"] == b"pdf-etag"
    assert document_request.meta["handle_httpstatus_list"] == [304]


def test_listing_record_uses_stored_etag_for_direct_document_request():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.crawler = SimpleNamespace(stats=Stats())
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    spider.parser = SimpleNamespace(
        parse_listing_page=lambda _html, _url: ListingPage(records=[listing_record]),
        get_next_page_url=lambda _html, _url: None,
    )
    spider.documents = SimpleNamespace(
        get_latest_etag=lambda _identifier, _section_id, _url: "html-etag"
    )
    response = HtmlResponse(
        url="https://example.com/listing",
        request=Request("https://example.com/listing"),
        body=b"<html></html>",
        encoding="utf-8",
    )

    yielded = list(spider.handle_listing(response))

    detail_request = yielded[0]
    assert detail_request.headers["If-None-Match"] == b"html-etag"
    assert detail_request.meta["handle_httpstatus_list"] == [304]


def test_not_modified_detail_response_is_counted_and_skipped():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.crawler = SimpleNamespace(stats=Stats())
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    response = HtmlResponse(
        url=listing_record.detail_link,
        status=304,
        request=Request(
            listing_record.detail_link,
            meta={"listing_record": listing_record},
        ),
    )

    yielded = list(spider.handle_detail(response))

    assert yielded == []
    assert spider.crawler.stats.values == {"kedra/skipped_unchanged": 1}


def test_not_modified_linked_document_is_counted_and_skipped():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.crawler = SimpleNamespace(stats=Stats())
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    docref = DocRef(
        url="https://example.com/decision-1.pdf",
        doc_type="pdf",
    )
    response = HtmlResponse(
        url=docref.url,
        status=304,
        request=Request(
            docref.url,
            meta={"listing_record": listing_record, "docref": docref},
        ),
    )

    yielded = list(spider.handle_document(response))

    assert yielded == []
    assert spider.crawler.stats.values == {"kedra/skipped_unchanged": 1}


def test_document_item_captures_response_etag():
    spider = SourceSpider(
        section="test",
        run_id="run-1",
        start="2026-01-01",
        end="2026-01-31",
    )
    spider.source_config = SimpleNamespace(source_id="test-source")
    listing_record = ListingRecord(
        identifier="decision-1",
        title="Decision title",
        description="Decision description",
        published_date=date(2026, 1, 15),
        detail_link="https://example.com/decision-1",
    )
    docref = DocRef(
        url="https://example.com/decision-1.pdf",
        doc_type="pdf",
    )
    response = HtmlResponse(
        url=docref.url,
        request=Request(
            docref.url,
            meta={"listing_record": listing_record, "docref": docref},
        ),
        headers={"ETag": "stored-etag", "Content-Type": "application/pdf"},
        body=b"%PDF-1.7 content",
    )

    item = list(spider.handle_document(response))[0]

    assert item.etag == "stored-etag"
