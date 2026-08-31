"""MinIO object store access. The write ordering rule lives with the callers:
object store put must succeed before the Mongo row exists."""

import io

from minio import Minio
from minio.error import S3Error

from kedra_scraper.config import get_settings


class ObjectStore:
    def __init__(self):
        self.runtime_settings = get_settings()
        self.minio_client = Minio(
            self.runtime_settings.minio_endpoint,
            access_key=self.runtime_settings.minio_access_key,
            secret_key=self.runtime_settings.minio_secret_key,
            secure=self.runtime_settings.minio_secure,
        )

    def ensure_buckets(self) -> None:
        for bucket in (
            self.runtime_settings.landing_bucket,
            self.runtime_settings.transformed_bucket,
        ):
            if not self.minio_client.bucket_exists(bucket):
                self.minio_client.make_bucket(bucket)

    def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        self.minio_client.put_object(
            bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        return f"{bucket}/{key}"

    def get_bytes(self, bucket: str, key: str) -> bytes:
        object_response = self.minio_client.get_object(bucket, key)
        try:
            return object_response.read()
        finally:
            object_response.close()
            object_response.release_conn()

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.minio_client.stat_object(bucket, key)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise

    def remove(self, bucket: str, key: str) -> None:
        self.minio_client.remove_object(bucket, key)
