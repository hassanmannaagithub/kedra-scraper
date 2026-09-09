from datetime import date
from types import SimpleNamespace

import pytest
from parsel import Selector
from scrapy.http import HtmlResponse

from kedra_scraper.scraper.parser import SelectorMatchError, SourceParser
from kedra_scraper.services.config import DateRangeConfig


def make_parser(*, start_inclusive=True, end_inclusive=True):
    selectors = SimpleNamespace(
        listing_row=".listing-row",
        empty_result=".empty-results",
        identifier=".identifier",
        title=".title",
        detail_link="a.detail-link",
        description=".description",
        published_date=".published-date",
        next_page=".next",
    )
    section_config = SimpleNamespace(
        url=(
            "https://www.workplacerelations.ie/en/search/"
            "?decisions=1&body=15376"
        ),
        selectors=selectors,
    )
    source_config = SimpleNamespace(
        date_range=DateRangeConfig(
            start_inclusive=start_inclusive,
            end_inclusive=end_inclusive,
        ),
        listing_params={
            "date_format": "%d/%m/%Y",
            "date_from": "from",
            "date_to": "to",
            "page": "pageNumber",
        },
    )

    return SourceParser(source_config, section_config)


def test_parse_listing_page_accepts_an_explicit_empty_result():
    parser = make_parser()

    listing_page = parser.parse_listing_page(
        HtmlResponse(
            url="https://example.com/listing",
            body='<div class="empty-results">There are no search results</div>',
            encoding="utf-8",
        )
    )

    assert listing_page.records == []
    assert listing_page.errors == []


def test_build_listing_url_preserves_section_parameters():
    parser = make_parser()

    listing_url = parser.build_listing_url(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        page_number=2,
    )

    assert listing_url == (
        "https://www.workplacerelations.ie/en/search/"
        "?decisions=1&body=15376&from=01/01/2026&to=31/01/2026&pageNumber=2"
    )


def test_build_listing_url_adapts_exclusive_source_boundaries():
    parser = make_parser(start_inclusive=False, end_inclusive=False)

    listing_url = parser.build_listing_url(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    assert listing_url == (
        "https://www.workplacerelations.ie/en/search/"
        "?decisions=1&body=15376&from=31/12/2025&to=01/02/2026&pageNumber=1"
    )


@pytest.mark.parametrize(
    ("start_inclusive", "end_inclusive", "expected_dates"),
    [
        (False, True, "from=31/12/2025&to=31/01/2026"),
        (True, False, "from=01/01/2026&to=01/02/2026"),
    ],
)
def test_build_listing_url_adapts_each_exclusive_boundary_independently(
    start_inclusive,
    end_inclusive,
    expected_dates,
):
    parser = make_parser(
        start_inclusive=start_inclusive,
        end_inclusive=end_inclusive,
    )

    listing_url = parser.build_listing_url(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    assert expected_dates in listing_url


def test_parse_listing_record_rejects_multiple_detail_links():
    parser = make_parser()
    listing_record = Selector(
        text="""
        <article>
            <span class="identifier">decision-1</span>
            <h2 class="title">Decision title</h2>
            <a class="detail-link" href="/first">First</a>
            <a class="detail-link" href="/second">Second</a>
        </article>
        """
    ).css("article")[0]

    with pytest.raises(SelectorMatchError, match="matched 2 elements"):
        parser.parse_listing_record(
            listing_record,
            "https://example.com/listing",
        )


def test_parse_listing_record_requires_title_from_its_own_selector():
    parser = make_parser()
    listing_record = Selector(
        text="""
        <article>
            <span class="identifier">decision-1</span>
            <a class="detail-link" href="/decision-1">decision-1</a>
            <p class="description">Decision description</p>
            <span class="published-date">31/08/2026</span>
        </article>
        """
    ).css("article")[0]

    with pytest.raises(
        SelectorMatchError,
        match="title '.title' matched no text",
    ):
        parser.parse_listing_record(
            listing_record,
            "https://example.com/listing",
        )


def test_parse_listing_record_requires_description():
    parser = make_parser()
    listing_record = Selector(
        text="""
        <article>
            <span class="identifier">decision-1</span>
            <h2 class="title">Decision title</h2>
            <a class="detail-link" href="/decision-1">View decision</a>
            <span class="published-date">31/08/2026</span>
        </article>
        """
    ).css("article")[0]

    with pytest.raises(
        SelectorMatchError,
        match="description '.description' matched no text",
    ):
        parser.parse_listing_record(
            listing_record,
            "https://example.com/listing",
        )


def test_parse_listing_record_requires_published_date():
    parser = make_parser()
    listing_record = Selector(
        text="""
        <article>
            <span class="identifier">decision-1</span>
            <h2 class="title">Decision title</h2>
            <a class="detail-link" href="/decision-1">View decision</a>
            <p class="description">Decision description</p>
        </article>
        """
    ).css("article")[0]

    with pytest.raises(
        SelectorMatchError,
        match="published_date '.published-date' matched no text",
    ):
        parser.parse_listing_record(
            listing_record,
            "https://example.com/listing",
        )


def test_parse_listing_record_rejects_invalid_published_date():
    parser = make_parser()
    listing_record = Selector(
        text="""
        <article>
            <span class="identifier">decision-1</span>
            <h2 class="title">Decision title</h2>
            <a class="detail-link" href="/decision-1">View decision</a>
            <p class="description">Decision description</p>
            <span class="published-date">not a date</span>
        </article>
        """
    ).css("article")[0]

    with pytest.raises(
        SelectorMatchError,
        match="published_date '.published-date' matched invalid date",
    ):
        parser.parse_listing_record(
            listing_record,
            "https://example.com/listing",
        )


def test_parse_listing_record_requires_detail_link():
    parser = make_parser()
    listing_record = Selector(
        text="""
        <article>
            <span class="identifier">decision-1</span>
            <h2 class="title">Decision title</h2>
            <p class="description">Decision description</p>
            <span class="published-date">31/08/2026</span>
        </article>
        """
    ).css("article")[0]

    with pytest.raises(
        SelectorMatchError,
        match="detail_link 'a.detail-link' matched no href",
    ):
        parser.parse_listing_record(
            listing_record,
            "https://example.com/listing",
        )


def test_get_next_page_url_returns_the_next_link_href():
    parser = make_parser()
    html = """
    <nav>
        <a href="?pageNumber=1">Previous</a>
        <a href="?pageNumber=3">Later</a>
        <a class="next" href="?pageNumber=2">Next</a>
    </nav>
    """

    next_page_url = parser.get_next_page_url(
        HtmlResponse(
            url="https://example.com/listing?pageNumber=1",
            body=html,
            encoding="utf-8",
        )
    )

    assert next_page_url == "https://example.com/listing?pageNumber=2"
