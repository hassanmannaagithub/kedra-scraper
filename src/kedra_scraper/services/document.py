"""Document slice: the write contract for ``landing_zone.documents`` and the
service that owns it — dedup lookups, version assignment, append-only insert.
The landing zone is append-only by design — fixes happen by re-scraping or
re-transforming, never by editing, so there is no update or delete here."""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pymongo.asynchronous.cursor import AsyncCursor

from kedra_scraper.utils.db import Databases, bson_safe


class Document(BaseModel):
    """One row of ``landing_zone.documents`` — append-only, one row per stored
    version. Unchanged documents write nothing here; a skip only shows up
    in the run's document counts."""

    model_config = ConfigDict(extra="forbid")

    identifier: str
    version: int = Field(ge=1)
    source_id: str
    section_id: str
    title: str
    description: str = ""
    published_date: Optional[date] = None
    partition_date: date
    source_url: str
    doc_link: str
    doc_type: str
    file_path: Optional[str] = None
    file_hash: Optional[str] = None
    etag: Optional[str] = None
    proxy: Optional[str] = None
    status: Literal["stored", "failed"]
    error: Optional[dict] = None
    scraped_at: datetime
    run_id: str


class DocumentService:
    """Async document access; the caller owns the database client's lifetime."""

    def __init__(self, databases: Databases):
        self.databases = databases

    def get_stored_documents_by_partition_range(
        self,
        partition_start: date,
        partition_end: date,
        section_id: str,
    ) -> AsyncCursor:
        """Build a cursor for stored rows, oldest version first.

        This does not perform I/O; callers fetch rows with ``async for``.
        """
        partition_query = {
            "status": "stored",
            "section_id": section_id,
            "partition_date": {
                "$gte": partition_start,
                "$lte": partition_end,
            },
        }
        partition_query = bson_safe(partition_query)
        return self.databases.documents.find(partition_query).sort(
            [("identifier", 1), ("version", 1)]
        )

    async def content_exists(self, identifier: str, file_hash: str) -> bool:
        document = await self.databases.documents.find_one(
            {"identifier": identifier, "file_hash": file_hash, "status": "stored"}
        )
        return document is not None

    async def get_latest_etag(
        self,
        identifier: str,
        section_id: str,
        document_url: str,
    ) -> Optional[str]:
        latest_document = await self.databases.documents.find_one(
            {
                "identifier": identifier,
                "section_id": section_id,
                "doc_link": document_url,
                "status": "stored",
            },
            sort=[("version", -1)],
        )
        if latest_document is None:
            return None
        return latest_document.get("etag")

    async def next_version(self, identifier: str) -> int:
        latest_document = await self.databases.documents.find_one(
            {"identifier": identifier}, sort=[("version", -1)]
        )
        return latest_document["version"] + 1 if latest_document else 1

    async def insert(self, document: Document) -> None:
        await self.databases.documents.insert_one(bson_safe(document.model_dump()))
