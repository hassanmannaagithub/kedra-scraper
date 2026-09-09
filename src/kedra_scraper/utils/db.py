"""Mongo client factory, logical DB handles, index bootstrap."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from kedra_scraper.config import get_settings


@dataclass
class Databases:
    """Async database handles sharing one client, owned by the calling scope."""

    landing: AsyncDatabase
    transformed: AsyncDatabase
    meta: AsyncDatabase

    async def __aenter__(self) -> "Databases":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    async def close(self) -> None:
        await self.landing.client.close()

    @property
    def documents(self) -> AsyncCollection:
        return self.landing.documents

    @property
    def transformed_documents(self) -> AsyncCollection:
        return self.transformed.documents

    @property
    def run_summaries(self) -> AsyncCollection:
        return self.meta.run_summaries


def get_databases() -> Databases:
    runtime_settings = get_settings()
    mongo_client = AsyncMongoClient(runtime_settings.mongo_uri, tz_aware=True)
    return Databases(
        landing=mongo_client[runtime_settings.landing_db],
        transformed=mongo_client[runtime_settings.transformed_db],
        meta=mongo_client[runtime_settings.meta_db],
    )


async def ensure_indexes(databases: Databases) -> None:
    await databases.documents.create_index(
        [("identifier", ASCENDING), ("version", ASCENDING)], unique=True
    )
    await databases.documents.create_index(
        [("identifier", ASCENDING), ("file_hash", ASCENDING)]
    )
    await databases.documents.create_index(
        [("partition_date", ASCENDING), ("section_id", ASCENDING)]
    )

    await databases.transformed_documents.create_index(
        [("source_file_path", ASCENDING)],
        unique=True,
    )

    await databases.run_summaries.create_index([("run_id", ASCENDING)], unique=True)


def bson_safe(value):
    """Mongo cannot encode datetime.date — promote to UTC midnight, recursively."""
    if isinstance(value, dict):
        return {k: bson_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [bson_safe(v) for v in value]
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return value
