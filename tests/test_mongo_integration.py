"""Optional local-stack check; each run owns disposable databases and buckets."""

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from threading import Thread
from uuid import uuid4

from minio import Minio
from pymongo import AsyncMongoClient
import pytest
import yaml


@pytest.mark.skipif(
    not os.environ.get("KEDRA_TEST_MONGO_URI")
    or not os.environ.get("KEDRA_TEST_MINIO_ENDPOINT"),
    reason="Set KEDRA_TEST_MONGO_URI and KEDRA_TEST_MINIO_ENDPOINT for local-stack tests",
)
def test_crawl_etag_transform_and_run_summary(tmp_path):
    prefix = f"kedra-test-{uuid4().hex}"
    database_names = [f"{prefix}-{zone}" for zone in ("landing", "transformed", "meta")]
    bucket_names = [f"{prefix}-{zone}" for zone in ("landing", "transformed")]
    conditional_requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/listing"):
                content = b'''<ul><li class="each-item">
                    <span class="refNO">ADJ-TEST-1</span>
                    <h2 class="title"><a href="/decision">Test decision</a></h2>
                    <p class="description">Test description</p>
                    <span class="date">15/01/2026</span>
                    </li></ul>'''
            else:
                etag = self.headers.get("If-None-Match")
                conditional_requests.append(etag)
                if etag == '"test-v1"':
                    self.send_response(304)
                    self.end_headers()
                    return
                content = b'<html><div class="content"><p>Decision</p></div></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("ETag", '"test-v1"')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    config = yaml.safe_load(Path("config/sources.yaml").read_text())
    source = config["sources"]["wrc"]
    source.update(
        base_url=base_url, robots_obey=False, download_delay=0,
        autothrottle_enabled=False, retry_enabled=False,
    )
    source["sections"] = {"wrc_adjudication": source["sections"]["wrc_adjudication"]}
    source["sections"]["wrc_adjudication"]["url"] = f"{base_url}/listing"
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(yaml.safe_dump(config))
    mongo_uri = os.environ["KEDRA_TEST_MONGO_URI"]
    endpoint = os.environ["KEDRA_TEST_MINIO_ENDPOINT"]
    env = {
        **os.environ,
        "KEDRA_CONFIG_PATH": str(config_path),
        "KEDRA_MONGO_URI": mongo_uri,
        "KEDRA_LANDING_DB": database_names[0],
        "KEDRA_TRANSFORMED_DB": database_names[1],
        "KEDRA_META_DB": database_names[2],
        "KEDRA_MINIO_ENDPOINT": endpoint,
        "KEDRA_MINIO_ACCESS_KEY": "minioadmin",
        "KEDRA_MINIO_SECRET_KEY": "minioadmin",
        "KEDRA_MINIO_SECURE": "false",
        "KEDRA_LANDING_BUCKET": bucket_names[0],
        "KEDRA_TRANSFORMED_BUCKET": bucket_names[1],
        "KEDRA_LOG_LEVEL": "ERROR",
    }
    objects = Minio(endpoint, access_key="minioadmin", secret_key="minioadmin", secure=False)

    def run_module(module, run_id):
        result = subprocess.run(
            [sys.executable, "-m", module, "--section", "wrc_adjudication",
             "--start", "2026-01-01", "--end", "2026-01-31", "--run-id", run_id],
            env=env, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "was never awaited" not in result.stderr
        return result.stdout

    async def check_documents():
        async with AsyncMongoClient(mongo_uri) as client:
            landing = await client[database_names[0]].documents.find({}).to_list()
            transformed = await client[database_names[1]].documents.find({}).to_list()
            summaries = client[database_names[2]].run_summaries
            assert len(landing) == len(transformed) == 1
            assert landing[0]["etag"] == '"test-v1"'
            assert transformed[0]["source_file_path"] == landing[0]["file_path"]
            assert (await summaries.find_one({"run_id": "first"}))["totals"]["scraped"] == 1
            assert (await summaries.find_one({"run_id": "second"}))["totals"]["skipped_unchanged"] == 1

    async def clean_databases():
        async with AsyncMongoClient(mongo_uri) as client:
            for name in database_names:
                await client.drop_database(name)

    try:
        run_module("kedra_scraper.scraper.runner", "first")
        run_module("kedra_scraper.scraper.runner", "second")
        assert conditional_requests == [None, '"test-v1"']
        output = run_module("kedra_scraper.transform", "transform-first")
        assert json.loads(output.splitlines()[-1])["transformed"] == 1
        output = run_module("kedra_scraper.transform", "transform-second")
        assert json.loads(output.splitlines()[-1])["skipped"] == 1
        asyncio.run(check_documents())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        asyncio.run(clean_databases())
        for bucket in bucket_names:
            if objects.bucket_exists(bucket):
                for entry in objects.list_objects(bucket, recursive=True):
                    objects.remove_object(bucket, entry.object_name)
                objects.remove_bucket(bucket)
