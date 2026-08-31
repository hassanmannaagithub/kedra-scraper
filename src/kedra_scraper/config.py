"""Env → typed settings. Connections, runtime knobs, and the path to the
config file — selectors and per-source behaviour live there, never here."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KEDRA_", env_file=".env", extra="ignore"
    )

    config_path: str = "config/sources.yaml"

    mongo_uri: str = "mongodb://ingest_admin:ingest_admin@localhost:27017/?authSource=admin"
    landing_db: str = "landing_zone"
    transformed_db: str = "transformed"
    meta_db: str = "meta"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    landing_bucket: str = "landing-zone"
    transformed_bucket: str = "transformed"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
