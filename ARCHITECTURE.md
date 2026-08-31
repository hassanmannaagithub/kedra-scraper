# Architecture

Kedra is a config-driven pipeline for Workplace Relations decisions. Source
URLs, selectors, partition sizes, and Scrapy controls live in
`config/sources.yaml`; connection and storage settings come from `KEDRA_*`
environment variables. MongoDB stores metadata, MinIO stores document bytes,
and the landing zone is append-only.

```text
sources.yaml -> Airflow partitions -> Scrapy -> landing metadata + objects
                                      |
                                      +-> JSON logs and run summary

landing metadata + objects -> transform -> transformed metadata + objects
```

## Date partitions

A unit of work is one `(section, date window)` and its start date becomes each
record's `partition_date`. Active, higher-volume bodies use 30-day windows;
lower-volume historical bodies use 365-day windows. These sizes keep listing
pagination manageable, make retries cheaper than repeating a year, and avoid
creating hundreds of empty tasks for inactive bodies. Airflow maps partitions
independently, so the sizes can be tuned per section without changing code.

## Retries and rate limiting

Each Scrapy process allows two in-flight requests to the domain, has a
one-second download-delay floor, and enables AutoThrottle. It retries 408, 429,
and transient 5xx responses three times. Airflow runs at most five scrape
partitions concurrently and retries a failed partition twice after five
minutes. This bounds both per-process and aggregate pressure on the source.

A malformed decision becomes a failed metadata record and the crawl continues.
A failed listing page or missing selector fails the partition so Airflow can
retry it. A configured empty-result selector distinguishes a valid zero-result
window from a likely site redesign. Structured JSON logs include source,
section, partition, failures, and final found/stored/unchanged counts.

## Idempotency and transformation

Before landing HTML, comments are removed because the source includes volatile
request/cache values in otherwise unchanged pages. SHA-256 is then calculated
over the exact bytes written to MinIO. An existing `(identifier, hash)` skips
both upload and metadata insertion; changed content is appended as the next
version. Conditional requests use a stored ETag when the server supplies one,
but the content hash remains authoritative.

Transforms query one section and partition range. PDF, DOC, and DOCX bytes pass
through unchanged; HTML is reduced to the configured decision container. Every
output is renamed `identifier.ext`, rehashed, stored in the transformed bucket,
and recorded with its source object path. That path is the transformation
idempotency key, allowing a changed source version to be processed while exact
reruns are skipped. Landing data is never updated or deleted.

## Scaling to 50+ sources

For compatible static sites, adding versioned source configuration and fixture
tests is sufficient. Sources requiring JavaScript, POST searches, or
authentication should use a small handler interface instead of growing the
generic spider with conditionals. At higher scale, I would move from one
Airflow DAG to source-owned DAGs or dataset triggers, enforce shared per-domain
rate budgets, run Scrapy workers on a distributed queue, and add selector smoke
tests and operational alerts. MongoDB indexes and object prefixes would be
sharded by source only after measured volume justified the added complexity.
