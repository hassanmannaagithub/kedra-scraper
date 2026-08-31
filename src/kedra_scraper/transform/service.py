"""Transform use-case: diff query → MinIO fetch → dispatch on
``doc_type`` (HTML through the content extractor, PDF/DOC pass through) →
identifier-based rename → rehash → transformed bucket → transformed row.

All Mongo access goes through the entity services: candidates are read via
``DocumentService``, transformed rows are checked and written via
``TransformedDocumentService``.

Per-document failures are contained and counted; they leave no transformed
row, so a fixed config or code re-run picks them up via the same diff query.
"""

import re
from datetime import date, datetime, timezone

from kedra_scraper.config import get_settings
from kedra_scraper.services.config import ConfigService
from kedra_scraper.services.document import DocumentService
from kedra_scraper.services.transformed_document import (
    TransformedDocument,
    TransformedDocumentService,
)
from kedra_scraper.transform.parser import extract_content
from kedra_scraper.utils.db import get_databases
from kedra_scraper.utils.document_formats import (
    CONTENT_TYPE_BY_DOCUMENT_TYPE,
    EXTENSION_BY_DOCUMENT_TYPE,
)
from kedra_scraper.utils.hashing import sha256_bytes
from kedra_scraper.utils.log import get_logger
from kedra_scraper.utils.objects import ObjectStore

def normalize_identifier(raw_identifier: str) -> str:
    """Interpretation happens here, not at fetch time: the landing zone keeps
    the site's raw rendering; transformed rows get one canonical identity
    (collapse whitespace, tighten hyphens, uppercase — WRC renders the same
    ref as 'IR - SC - 00003328' and 'ir-sc-00003328')."""
    normalized_identifier = " ".join(raw_identifier.split())
    normalized_identifier = re.sub(
        r"[\u2010\u2011\u2012\u2013\u2014]",
        "-",
        normalized_identifier,
    )
    return re.sub(r"\s*-\s*", "-", normalized_identifier).upper()


class TransformService:
    def __init__(self):
        self.config_service = ConfigService()
        databases = get_databases()
        self.document_service = DocumentService(databases)
        self.transformed_document_service = TransformedDocumentService(databases)
        self.object_store = ObjectStore()
        self.settings = get_settings()

    def run(
        self,
        start_date: date,
        end_date: date,
        run_id: str,
        section_id: str,
    ) -> dict:
        if end_date < start_date:
            raise ValueError(f"end {end_date} before start {start_date}")
        self.object_store.ensure_buckets()
        logger = get_logger(
            "kedra.transform",
            run_id=run_id,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            section_id=section_id,
        )

        transformation_counts = {
            "candidates": 0,
            "transformed": 0,
            "skipped": 0,
            "failed": 0,
        }
        logger.info("transform start")
        source_documents = self.document_service.get_stored_documents_by_partition_range(
            start_date,
            end_date,
            section_id,
        )
        for source_document in source_documents:
            transformation_counts["candidates"] += 1
            already_transformed = (
                self.transformed_document_service.exists_for_source_file(
                    source_document["file_path"],
                )
            )
            if already_transformed:
                transformation_counts["skipped"] += 1
                continue
            try:
                self._transform_document(
                    source_document,
                    run_id,
                )
                transformation_counts["transformed"] += 1
            except Exception as exc:
                transformation_counts["failed"] += 1
                logger.error(
                    "transform failed",
                    identifier=source_document["identifier"],
                    version=source_document["version"],
                    stage="transform",
                    error=f"{type(exc).__name__}: {exc}",
                    url=source_document.get("doc_link"),
                )
        logger.info("transform end", **transformation_counts)
        return transformation_counts

    def _transform_document(
        self,
        source_document: dict,
        run_id: str,
    ) -> None:
        source_bucket, source_key = source_document["file_path"].split("/", 1)
        source_content = self.object_store.get_bytes(source_bucket, source_key)
        transformed_content = self._get_transformed_content(
            source_document,
            source_content,
        )

        normalized_identifier = normalize_identifier(
            source_document["identifier"]
        )
        object_key = self._build_object_key(
            source_document,
            normalized_identifier,
        )
        transformed_file_hash = sha256_bytes(transformed_content)
        content_type = CONTENT_TYPE_BY_DOCUMENT_TYPE.get(
            source_document["doc_type"],
            "application/octet-stream",
        )
        transformed_file_path = self.object_store.put_bytes(
            self.settings.transformed_bucket,
            object_key,
            transformed_content,
            content_type,
        )

        transformed_document = self._build_transformed_document(
            source_document,
            normalized_identifier,
            transformed_file_path,
            transformed_file_hash,
            run_id,
        )
        self.transformed_document_service.insert(transformed_document)

    def _get_transformed_content(
        self,
        source_document: dict,
        source_content: bytes,
    ) -> bytes:
        if source_document["doc_type"] != "html":
            return source_content

        section_config = self.config_service.get_section(
            source_document["section_id"]
        )
        extracted_html = extract_content(
            source_content.decode("utf-8", errors="replace"),
            section_config.selectors.content_container,
            source_document.get("source_url", ""),
        )
        return extracted_html.encode("utf-8")

    @staticmethod
    def _build_object_key(
        source_document: dict,
        normalized_identifier: str,
    ) -> str:
        storage_safe_identifier = normalized_identifier.replace(
            "/",
            "_",
        ).replace(" ", "_")
        extension = EXTENSION_BY_DOCUMENT_TYPE.get(
            source_document["doc_type"],
            "bin",
        )
        partition_date = source_document["partition_date"].date().isoformat()
        version_directory = f"v{source_document['version']}"

        return (
            f"{source_document['section_id']}/{partition_date}/"
            f"{version_directory}/{storage_safe_identifier}.{extension}"
        )

    @staticmethod
    def _build_transformed_document(
        source_document: dict,
        normalized_identifier: str,
        transformed_file_path: str,
        transformed_file_hash: str,
        run_id: str,
    ) -> TransformedDocument:
        published_date = None
        if source_document.get("published_date"):
            published_date = source_document["published_date"].date()

        return TransformedDocument(
            identifier=normalized_identifier,
            version=source_document["version"],
            source_id=source_document["source_id"],
            section_id=source_document["section_id"],
            title=source_document.get("title", ""),
            description=source_document.get("description", ""),
            published_date=published_date,
            partition_date=source_document["partition_date"].date(),
            source_url=source_document["source_url"],
            doc_link=source_document["doc_link"],
            doc_type=source_document["doc_type"],
            source_file_path=source_document["file_path"],
            source_file_hash=source_document["file_hash"],
            file_path=transformed_file_path,
            file_hash=transformed_file_hash,
            transformed_at=datetime.now(timezone.utc),
            run_id=run_id,
        )
