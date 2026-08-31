"""Mongo client factory, logical DB handles, index bootstrap."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from kedra_scraper.config import get_settings


def get_mongo_client() -> MongoClient:
    return MongoClient(get_settings().mongo_uri, tz_aware=True)


@dataclass
class Databases:
    landing: Database
    transformed: Database
    meta: Database

    @property
    def documents(self) -> Collection:
        return self.landing.documents

    @property
    def transformed_documents(self) -> Collection:
        return self.transformed.documents

    @property
    def run_summaries(self) -> Collection:
        return self.meta.run_summaries


def get_databases() -> Databases:
    runtime_settings = get_settings()
    mongo_client = get_mongo_client()
    return Databases(
        landing=mongo_client[runtime_settings.landing_db],
        transformed=mongo_client[runtime_settings.transformed_db],
        meta=mongo_client[runtime_settings.meta_db],
    )


def ensure_indexes(databases: Databases) -> None:
    databases.documents.create_index(
        [("identifier", ASCENDING), ("version", ASCENDING)], unique=True
    )
    databases.documents.create_index(
        [("identifier", ASCENDING), ("file_hash", ASCENDING)]
    )
    databases.documents.create_index(
        [("partition_date", ASCENDING), ("section_id", ASCENDING)]
    )

    # One transformed result per landing object.
    databases.transformed_documents.create_index(
        [("source_file_path", ASCENDING)],
        unique=True,
    )

    databases.run_summaries.create_index([("run_id", ASCENDING)], unique=True)


def bson_safe(value):
    """Mongo cannot encode datetime.date — promote to UTC midnight, recursively."""
    if isinstance(value, dict):
        return {k: bson_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [bson_safe(v) for v in value]
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return value
