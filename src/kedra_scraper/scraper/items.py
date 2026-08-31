"""One item type, mirroring the document slice's ``Document``. No per-source
items. ``content`` rides along for the pipelines and never reaches Mongo;
``version``/``file_path``/``file_hash`` are filled by the pipelines."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class DocumentItem:
    identifier: str
    title: str
    description: str
    published_date: Optional[date]
    source_id: str
    section_id: str
    partition_date: date
    source_url: str
    doc_link: str
    doc_type: str
    status: str  # "stored" | "failed"
    run_id: str
    scraped_at: datetime
    content: Optional[bytes] = None
    error: Optional[dict] = None
    version: Optional[int] = None
    file_path: Optional[str] = None
    file_hash: Optional[str] = None
    etag: Optional[str] = None
    proxy: Optional[str] = None  # host:port only, never credentials
