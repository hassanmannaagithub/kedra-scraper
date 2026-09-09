# kedra-scraper

A configurable Scrapy pipeline that collects legal decisions from Ireland's
Workplace Relations website. It stores immutable source documents and metadata
in MinIO and MongoDB, then produces cleaned, identifier-named documents in a
separate transformed zone. Airflow is included for partitioned orchestration.

## What is included

- Four decision bodies configured in `config/sources.yaml`
- Date-window partitioning with a `partition_date` on every record
- HTML, PDF, DOC, and DOCX document storage
- SHA-256 change detection and idempotent reruns
- JSON logs and per-partition run summaries
- Separate landing and transformed MongoDB collections and MinIO buckets
- Airflow orchestration with bounded parallelism and task dependencies

## Prerequisites

- Docker Desktop with Docker Compose v2
- About 4 GB of free memory when running Airflow

Python is not required on the host for the Docker workflow.

## Quick start

Start MongoDB and MinIO:

```bash
docker compose up -d mongo minio
```

Run the test suite:

```bash
docker compose run --build --rm test
```

To also test a complete crawl and transform against the local MongoDB and
MinIO services, run the following after starting them. The test serves a local
page, uses uniquely named test databases and buckets, and removes them afterward.

```bash
docker compose run --build --rm \
  -e 'KEDRA_TEST_MONGO_URI=mongodb://root:root@mongo:27017/?authSource=admin' \
  -e KEDRA_TEST_MINIO_ENDPOINT=minio:9000 test
```

Scrape one section and inclusive date window:

```bash
docker compose run --build --rm scraper \
  --section wrc_adjudication \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --run-id manual-2024-01
```

Transform the documents stored by that partition:

```bash
docker compose run --build --rm transform \
  --section wrc_adjudication \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --run-id manual-2024-01-transform
```

Valid section names are `wrc_adjudication`, `labour_court`,
`employment_appeals_tribunal`, and `equality_tribunal`.

MinIO is available at [http://localhost:9001](http://localhost:9001) with the
development credentials `minioadmin` / `minioadmin`. MongoDB listens on port
`27017`; the development connection values are documented in `.env.example`.

## Run with Airflow

Start the complete stack:

```bash
docker compose --profile airflow up --build -d
docker compose logs airflow
```

Open [http://localhost:8080](http://localhost:8080). The standalone Airflow
username and password are printed in the `airflow` service logs. Trigger
`ingest_pipeline` with a payload such as:

```json
{
  "source_id": "wrc",
  "start_date": "2024-01-01",
  "end_date": "2024-01-31"
}
```

Airflow expands the range into section-specific partitions. Each transform
starts after its matching scrape attempt, so successfully landed documents can
still be transformed if another document in the partition fails.

## Configuration

`config/sources.yaml` contains source URLs, selectors, partition sizes, request
concurrency, delays, retries, AutoThrottle, user agent, proxy settings, and date
filter semantics. `date_range.start_inclusive` and `date_range.end_inclusive`
describe how each source interprets its filter values. Pipeline partitions stay
inclusive; for an exclusive source boundary, the request value is moved outward
by one day so adjacent partitions contain neither gaps nor unintended overlap.
Connection strings, database names, bucket names, and log level use `KEDRA_*`
environment variables; see `.env.example` for every supported value.

For a local, non-Docker Python workflow:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1` and copy
the environment file with `Copy-Item .env.example .env`.

## Storage model and reruns

Landing documents are append-only. A new object and metadata version are
created only when the identifier's normalized bytes produce a new SHA-256
hash. HTML comments are removed before storage and hashing because the source
site emits volatile request metadata in comments. Failed downloads are stored
as metadata records with their URL and reason; they do not stop unrelated
documents from completing.

Transformed HTML keeps only the configured decision container. Binary files
pass through unchanged. All transformed files are named `identifier.ext` and
are skipped on rerun when their source object path was already processed.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design decisions and scaling
trade-offs.

## Stop the stack

```bash
docker compose --profile airflow down
```

Add `--volumes` only when you intentionally want to delete all local MongoDB,
MinIO, PostgreSQL, and Airflow data.
