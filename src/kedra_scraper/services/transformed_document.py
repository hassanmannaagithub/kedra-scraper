"""Transformed-document slice: the write contract for
``transformed.documents`` and the service that owns it. Append-only like the
landing zone — a re-transform writes a new row, never edits one."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from kedra_scraper.utils.db import Databases, bson_safe


class TransformedDocument(BaseModel):
    """One row of ``transformed.documents``. Self-contained: full metadata
    carried forward, plus provenance of the source object."""

    model_config = ConfigDict(extra="forbid")

    identifier: str
    version: int
    source_id: str
    section_id: str
    title: str
    description: str = ""
    published_date: Optional[date] = None
    partition_date: date
    source_url: str
    doc_link: str
    doc_type: str
    source_file_path: str
    source_file_hash: str
    file_path: str
    file_hash: str
    transformed_at: datetime
    run_id: str


class TransformedDocumentService:
    """The only writer of ``transformed.documents``."""

    def __init__(self, dbs: Databases):
        self.dbs = dbs

    async def exists_for_source_file(
        self,
        source_file_path: str,
    ) -> bool:
        document = await self.dbs.transformed_documents.find_one(
            {"source_file_path": source_file_path}
        )
        return document is not None

    async def insert(self, document: TransformedDocument) -> None:
        await self.dbs.transformed_documents.insert_one(bson_safe(document.model_dump()))
