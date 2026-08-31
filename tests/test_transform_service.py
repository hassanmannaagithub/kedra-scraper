from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import kedra_scraper.transform.service as transform_module
from kedra_scraper.transform.service import TransformService, normalize_identifier


class FakeLogger:
    def __init__(self):
        self.errors = []

    def info(self, _message, **_fields):
        pass

    def error(self, message, **fields):
        self.errors.append((message, fields))


class FakeDocumentService:
    def __init__(self, source_documents):
        self.source_documents = source_documents

    def get_stored_documents_by_partition_range(
        self,
        _start_date,
        _end_date,
        section_id,
    ):
        return iter(
            document
            for document in self.source_documents
            if document["section_id"] == section_id
        )


class FakeTransformedDocumentService:
    def __init__(self, existing_source_files):
        self.existing_source_files = set(existing_source_files)
        self.inserted_documents = []

    def exists_for_source_file(self, source_file_path):
        return source_file_path in self.existing_source_files

    def insert(self, document):
        self.inserted_documents.append(document)


class FakeObjectStore:
    def __init__(self, source_objects):
        self.source_objects = source_objects
        self.uploads = []
        self.buckets_ensured = False

    def ensure_buckets(self):
        self.buckets_ensured = True

    def get_bytes(self, bucket, key):
        return self.source_objects[(bucket, key)]

    def put_bytes(self, bucket, key, data, content_type):
        self.uploads.append((bucket, key, data, content_type))
        return f"{bucket}/{key}"


class FakeConfigService:
    def __init__(self):
        self.requested_sections = []

    def get_section(self, section_id):
        self.requested_sections.append(section_id)
        selectors = SimpleNamespace(content_container="div.content")
        return SimpleNamespace(selectors=selectors)


def build_source_document(
    identifier,
    version,
    doc_type,
    file_hash,
    file_path,
    section_id="wrc_adjudication",
):
    return {
        "identifier": identifier,
        "version": version,
        "source_id": "wrc",
        "section_id": section_id,
        "title": "Decision title",
        "description": "Decision description",
        "published_date": datetime(2026, 1, 15, tzinfo=timezone.utc),
        "partition_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source_url": "https://example.com/decision",
        "doc_link": "https://example.com/decision",
        "doc_type": doc_type,
        "file_path": file_path,
        "file_hash": file_hash,
    }


def build_service(
    monkeypatch,
    source_documents,
    source_objects,
    existing_source_files=None,
):
    if existing_source_files is None:
        existing_source_files = set()

    document_service = FakeDocumentService(source_documents)
    transformed_service = FakeTransformedDocumentService(existing_source_files)
    object_store = FakeObjectStore(source_objects)
    config_service = FakeConfigService()
    logger = FakeLogger()

    monkeypatch.setattr(transform_module, "get_databases", lambda: object())
    monkeypatch.setattr(
        transform_module,
        "DocumentService",
        lambda _databases: document_service,
    )
    monkeypatch.setattr(
        transform_module,
        "TransformedDocumentService",
        lambda _databases: transformed_service,
    )
    monkeypatch.setattr(
        transform_module,
        "ObjectStore",
        lambda: object_store,
    )
    monkeypatch.setattr(
        transform_module,
        "ConfigService",
        lambda: config_service,
    )
    monkeypatch.setattr(
        transform_module,
        "get_settings",
        lambda: SimpleNamespace(transformed_bucket="transformed"),
    )
    monkeypatch.setattr(
        transform_module,
        "get_logger",
        lambda *_args, **_kwargs: logger,
    )

    service = TransformService()

    return service, transformed_service, object_store, config_service, logger


def test_normalize_identifier_normalizes_whitespace_dashes_and_case():
    identifier = normalize_identifier("  ir - sc – 00004740  ")

    assert identifier == "IR-SC-00004740"


def test_run_transforms_only_the_requested_section(monkeypatch):
    wrc_document = build_source_document(
        identifier="ADJ-0001",
        version=1,
        doc_type="pdf",
        file_hash="wrc-hash",
        file_path="landing/wrc.pdf",
    )
    labour_document = build_source_document(
        identifier="LCR-0001",
        version=1,
        doc_type="pdf",
        file_hash="labour-hash",
        file_path="landing/labour.pdf",
        section_id="labour_court",
    )
    service, transformed_service, object_store, _config_service, _logger = (
        build_service(
            monkeypatch,
            [wrc_document, labour_document],
            {
                ("landing", "wrc.pdf"): b"wrc",
                ("landing", "labour.pdf"): b"labour",
            },
        )
    )

    counts = service.run(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        run_id="run-1",
        section_id="wrc_adjudication",
    )

    assert counts == {
        "candidates": 1,
        "transformed": 1,
        "skipped": 0,
        "failed": 0,
    }
    assert len(transformed_service.inserted_documents) == 1
    assert transformed_service.inserted_documents[0].section_id == "wrc_adjudication"
    assert len(object_store.uploads) == 1


def test_run_transforms_html_skips_transformed_source_and_contains_failure(
    monkeypatch,
):
    valid_document = build_source_document(
        identifier=" ir - sc - 00004740 ",
        version=1,
        doc_type="html",
        file_hash="new-hash",
        file_path="landing/valid.html",
    )
    skipped_document = build_source_document(
        identifier="ADJ-0001",
        version=1,
        doc_type="pdf",
        file_hash="existing-hash",
        file_path="landing/existing.pdf",
    )
    invalid_document = build_source_document(
        identifier="ADJ-0002",
        version=1,
        doc_type="html",
        file_hash="invalid-hash",
        file_path="landing/invalid.html",
    )
    source_objects = {
        ("landing", "valid.html"): (
            b"<html><div class='content'><p>Decision</p></div></html>"
        ),
        ("landing", "invalid.html"): b"<html><p>No container</p></html>",
    }
    service, transformed_service, object_store, config_service, logger = (
        build_service(
            monkeypatch,
            [valid_document, skipped_document, invalid_document],
            source_objects,
            existing_source_files={"landing/existing.pdf"},
        )
    )

    counts = service.run(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        run_id="run-1",
        section_id="wrc_adjudication",
    )

    assert counts == {
        "candidates": 3,
        "transformed": 1,
        "skipped": 1,
        "failed": 1,
    }
    assert object_store.buckets_ensured is True
    assert len(object_store.uploads) == 1

    bucket, key, content, content_type = object_store.uploads[0]
    assert bucket == "transformed"
    assert key == "wrc_adjudication/2026-01-01/v1/IR-SC-00004740.html"
    assert content == b'<div class="content"><p>Decision</p></div>'
    assert content_type == "text/html"

    assert len(transformed_service.inserted_documents) == 1
    transformed_document = transformed_service.inserted_documents[0]
    assert transformed_document.identifier == "IR-SC-00004740"
    assert transformed_document.source_file_hash == "new-hash"
    assert transformed_document.file_path == f"transformed/{key}"
    assert config_service.requested_sections == [
        "wrc_adjudication",
        "wrc_adjudication",
    ]
    assert logger.errors[0][1]["identifier"] == "ADJ-0002"


@pytest.mark.parametrize(
    ("doc_type", "document_content", "expected_content_type"),
    [
        ("pdf", b"%PDF-1.7 test content", "application/pdf"),
        ("doc", b"legacy Word content", "application/msword"),
        (
            "docx",
            b"zipped Word content",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
        ),
    ],
)
def test_run_passes_binary_content_through_unchanged(
    monkeypatch,
    doc_type,
    document_content,
    expected_content_type,
):
    source_document = build_source_document(
        identifier="ADJ-0003",
        version=2,
        doc_type=doc_type,
        file_hash=f"{doc_type}-hash",
        file_path=f"landing/decision.{doc_type}",
    )
    service, transformed_service, object_store, _config_service, _logger = (
        build_service(
            monkeypatch,
            [source_document],
            {("landing", f"decision.{doc_type}"): document_content},
        )
    )

    counts = service.run(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        run_id="run-1",
        section_id="wrc_adjudication",
    )

    assert counts["transformed"] == 1

    bucket, key, content, content_type = object_store.uploads[0]
    assert bucket == "transformed"
    assert key == f"wrc_adjudication/2026-01-01/v2/ADJ-0003.{doc_type}"
    assert content == document_content
    assert content_type == expected_content_type
    assert transformed_service.inserted_documents[0].doc_type == doc_type


def test_run_transforms_distinct_source_files_with_identical_content(monkeypatch):
    first_document = build_source_document(
        identifier="ADJ-0004",
        version=1,
        doc_type="pdf",
        file_hash="shared-hash",
        file_path="landing/first.pdf",
    )
    second_document = build_source_document(
        identifier="ADJ-0005",
        version=1,
        doc_type="pdf",
        file_hash="shared-hash",
        file_path="landing/second.pdf",
    )
    pdf_content = b"%PDF-1.7 shared content"
    service, transformed_service, object_store, _config_service, _logger = (
        build_service(
            monkeypatch,
            [first_document, second_document],
            {
                ("landing", "first.pdf"): pdf_content,
                ("landing", "second.pdf"): pdf_content,
            },
        )
    )

    counts = service.run(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        run_id="run-1",
        section_id="wrc_adjudication",
    )

    assert counts["transformed"] == 2
    assert counts["skipped"] == 0
    assert len(object_store.uploads) == 2
    assert len(transformed_service.inserted_documents) == 2
