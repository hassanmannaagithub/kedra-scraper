"""The config-driven parser for a source's listing and detail pages.

Every site-specific value — selectors, URL params, date format — comes from
``SourceConfig`` / ``SectionConfig``; the spider stays free of both. Parsing
uses parsel (same engine Scrapy responses use) so the parser runs identically
under the spider and offline fixture tests. BeautifulSoup stays confined to
transform/parser.py.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

from parsel import Selector

from kedra_scraper.utils.document_formats import (
    DOCUMENT_TYPE_BY_CONTENT_TYPE,
    DOCUMENT_TYPE_BY_EXTENSION,
)
from kedra_scraper.services.config import SectionConfig, SourceConfig


class SelectorMatchError(Exception):
    """A selector produced an invalid number of matches."""


@dataclass(frozen=True)
class ListingRecord:
    identifier: str
    title: str
    description: str
    published_date: date
    detail_link: str


@dataclass(frozen=True)
class ListingRowError:
    """One listing row that failed structurally — contained at layer 2 of the
    failure model: a bad row must cost a failed row, never the partition."""

    reason: str
    snippet: str


@dataclass(frozen=True)
class ListingPage:
    records: list[ListingRecord]
    errors: list[ListingRowError] = field(default_factory=list)


@dataclass(frozen=True)
class DocRef:
    url: str
    doc_type: Optional[str]


class SourceParser:
    def __init__(self, source_config: SourceConfig, section_config: SectionConfig):
        self.source_config = source_config
        self.section_config = section_config
        self.selectors = section_config.selectors

    def build_listing_url(
        self,
        start_date: date,
        end_date: date,
        page_number: int = 1,
    ) -> str:
        listing_parameters = self.source_config.listing_params
        date_format = listing_parameters["date_format"]
        date_range = self.source_config.date_range

        source_start_date = start_date
        if not date_range.start_inclusive:
            source_start_date -= timedelta(days=1)

        source_end_date = end_date
        if not date_range.end_inclusive:
            source_end_date += timedelta(days=1)

        start_date_text = source_start_date.strftime(date_format)
        end_date_text = source_end_date.strftime(date_format)

        section_url = urlsplit(self.section_config.url)
        query_parameters = dict(parse_qsl(section_url.query))

        start_date_parameter = listing_parameters["date_from"]
        end_date_parameter = listing_parameters["date_to"]
        page_parameter = listing_parameters["page"]

        query_parameters[start_date_parameter] = start_date_text
        query_parameters[end_date_parameter] = end_date_text
        query_parameters[page_parameter] = str(page_number)

        encoded_query = self.encode_query(query_parameters)

        updated_section_url = section_url._replace(query=encoded_query)
        listing_url = updated_section_url.geturl()

        return listing_url

    def encode_query(self, query_parameters: dict[str, str]) -> str:
        return urlencode(query_parameters, quote_via=quote, safe="/")

    def normalize_url(self, href: str, page_url: str) -> str:
        return urljoin(page_url, quote(href.strip(), safe="/:?&=%~"))

    def parse_listing_page(self, html: str, page_url: str) -> ListingPage:
        listing_record_selector = self.selectors.listing_row

        document = Selector(text=html)
        listing_record_elements = document.css(listing_record_selector)
        if not listing_record_elements:
            empty_result_selector = self.selectors.empty_result
            if empty_result_selector and document.css(empty_result_selector):
                return ListingPage(records=[])
            raise SelectorMatchError(
                f"listing_row {listing_record_selector!r} matched 0 rows at {page_url}"
            )
        records: list[ListingRecord] = []
        errors: list[ListingRowError] = []
        for listing_record_element in listing_record_elements:
            try:
                listing_record = self.parse_listing_record(
                    listing_record_element, page_url)
                records.append(listing_record)
            except SelectorMatchError as exc:
                errors.append(
                    ListingRowError(
                        reason=str(exc),
                        snippet=" ".join(
                            listing_record_element.get().split())[:300],
                    )
                )
        return ListingPage(records=records, errors=errors)

    def parse_listing_record(
        self,
        listing_record_element: Selector,
        page_url: str,
    ) -> ListingRecord:
        identifier_selector = self.selectors.identifier
        title_selector = self.selectors.title
        detail_link_selector = self.selectors.detail_link
        description_selector = self.selectors.description
        published_date_selector = self.selectors.published_date

        identifier = self.extract_first_text(
            html_element=listing_record_element,
            selector_candidates=identifier_selector,
            field_name="identifier",
            page_url=page_url,
        )

        detail_link_elements = listing_record_element.css(detail_link_selector)

        if len(detail_link_elements) > 1:
            message = (
                f"detail_link {detail_link_selector!r} "
                f"matched {len(detail_link_elements)} elements for {identifier!r} "
                f"at {page_url}"
            )

            raise SelectorMatchError(message)

        detail_link = detail_link_elements.attrib.get("href")

        if not detail_link:
            message = (
                f"detail_link {detail_link_selector!r} "
                f"matched no href for {identifier!r} "
                f"at {page_url}"
            )

            raise SelectorMatchError(message)

        detail_link_normalized = self.normalize_url(detail_link, page_url)

        title = self.extract_text(listing_record_element, title_selector)

        if not title:
            message = (
                f"title {title_selector!r} matched no text for {identifier!r} "
                f"at {page_url}"
            )

            raise SelectorMatchError(message)

        description = self.extract_text(
            listing_record_element, description_selector)

        if not description:
            message = (
                f"description {description_selector!r} matched no text "
                f"for {identifier!r} at {page_url}"
            )

            raise SelectorMatchError(message)

        published_date_text = self.extract_text(
            listing_record_element, published_date_selector)

        if not published_date_text:
            message = (
                f"published_date {published_date_selector!r} matched no text "
                f"for {identifier!r} at {page_url}"
            )

            raise SelectorMatchError(message)

        published_date = self.parse_date_text(published_date_text)

        if published_date is None:
            message = (
                f"published_date {published_date_selector!r} matched invalid date "
                f"{published_date_text!r} for {identifier!r} at {page_url}"
            )

            raise SelectorMatchError(message)

        return ListingRecord(
            identifier=identifier,
            title=title,
            description=description,
            published_date=published_date,
            detail_link=detail_link_normalized,
        )

    def extract_first_text(
        self,
        html_element: Selector,
        selector_candidates: str | list[str],
        field_name: str,
        page_url: str,
    ) -> str:
        if isinstance(selector_candidates, str):
            normalized_selectors = [selector_candidates]
        else:
            normalized_selectors = selector_candidates

        for selector in normalized_selectors:
            extracted_text = self.extract_text(html_element, selector)

            if extracted_text:
                return extracted_text

        message = (
            f"{field_name} selectors {normalized_selectors!r} "
            f"matched no text at {page_url}"
        )

        raise SelectorMatchError(message)

    def parse_date_text(
        self,
        date_text: str,
    ) -> Optional[date]:
        cleaned_date_text = date_text.strip()

        date_format = self.source_config.listing_params["date_format"]

        try:
            parsed_datetime = datetime.strptime(cleaned_date_text, date_format)
        except ValueError:
            return None

        parsed_date = parsed_datetime.date()

        return parsed_date

    def get_next_page_url(
        self,
        html: str,
        page_url: str,
    ) -> Optional[str]:
        document = Selector(text=html)
        next_page_selector = self.selectors.next_page
        next_page_elements = document.css(next_page_selector)

        if not next_page_elements:
            return None

        if len(next_page_elements) > 1:
            message = (
                f"next_page {next_page_selector!r} "
                f"matched {len(next_page_elements)} elements at {page_url}"
            )

            raise SelectorMatchError(message)

        next_page_href = next_page_elements.attrib.get("href")

        if not next_page_href:
            message = (
                f"next_page {next_page_selector!r} matched no href "
                f"at {page_url}"
            )

            raise SelectorMatchError(message)

        return self.normalize_url(next_page_href, page_url)

    def parse_detail_page(self, html: str, detail_url: str) -> DocRef:
        document_link_selector = self.selectors.doc_link

        if document_link_selector is None:
            return DocRef(url=detail_url, doc_type="html")

        document = Selector(text=html)
        document_link_elements = document.css(document_link_selector)
        document_href = document_link_elements.attrib.get("href")
        if not document_href:
            raise SelectorMatchError(
                f"doc_link {document_link_selector!r} matched no href at {detail_url}"
            )
        document_url = self.normalize_url(document_href, detail_url)
        return DocRef(url=document_url, doc_type=self.get_doc_type(document_url))

    def get_doc_type(
        self,
        document_url: str,
        content_type: Optional[str] = None,
    ) -> Optional[str]:
        """Doc type from the URL extension, else from a response Content-Type
        once the download happened. None means: sniff at download time."""
        url_path = urlsplit(document_url).path
        if "." in url_path.rsplit("/", 1)[-1]:
            extension = url_path.rsplit(".", 1)[-1].lower()
            if extension in DOCUMENT_TYPE_BY_EXTENSION:
                return DOCUMENT_TYPE_BY_EXTENSION[extension]
        if content_type:
            normalized_content_type = content_type.split(";")[
                0].strip().lower()
            return DOCUMENT_TYPE_BY_CONTENT_TYPE.get(normalized_content_type)
        return None

    @staticmethod
    def clean_text(text: str) -> str:
        words = text.split()
        cleaned_text = " ".join(words)

        return cleaned_text

    def extract_text(
        self,
        html_element: Selector,
        css_selector: str,
    ) -> str:
        text_fragments = html_element.css(f"{css_selector} ::text").getall()

        extracted_text = " ".join(text_fragments)

        cleaned_text = self.clean_text(extracted_text)

        return cleaned_text
