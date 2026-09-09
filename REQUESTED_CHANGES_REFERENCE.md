# Requested changes reference

## 1. Use `response.css()` directly

- [parser.py](src/kedra_scraper/scraper/parser.py): `parse_listing_page()`, `get_next_page_url()`, and `parse_detail_page()`.

## 2. How Scrapy sends yielded items to the pipeline

- Read [SCRAPY_PIPELINE_EXPLANATION.md](SCRAPY_PIPELINE_EXPLANATION.md).

## 3. Make `process_item()` async with PyMongo

- [SCRAPY_ASYNC_PIPELINE_EXPLANATION.md](SCRAPY_ASYNC_PIPELINE_EXPLANATION.md): explanation and documentation references.
- [pipelines.py](src/kedra_scraper/scraper/pipelines.py): async `process_item()`.
- [document.py](src/kedra_scraper/services/document.py): awaited MongoDB operations.
- [db.py](src/kedra_scraper/utils/db.py): `AsyncMongoClient` and cleanup.
- [settings.py](src/kedra_scraper/scraper/settings.py): `AsyncioSelectorReactor` configuration.
