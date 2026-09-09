# How we added async MongoDB to the Scrapy pipeline

Scrapy supports `async def process_item()`. We made our pipeline method async so it can `await` MongoDB operations. While an item waits for MongoDB, Scrapy can continue other work. See the [Scrapy pipeline docs](https://docs.scrapy.org/en/2.14/topics/item-pipeline.html).

In [db.py](src/kedra_scraper/utils/db.py), we added a factory for PyMongo's official `AsyncMongoClient`. We require `pymongo>=4.13`, where its async API became generally available. See the [PyMongo release notes](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/release-notes/#what-s-new-in-4.13).

We kept one [DocumentService](src/kedra_scraper/services/document.py). Its pipeline methods—`content_exists`, `next_version`, and `insert`—now await MongoDB's `find_one` and `insert_one`. See the [PyMongo async migration guide](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/#migrate-from-pymongo).

After checking for duplicates, the successful-item flow in [pipelines.py](src/kedra_scraper/scraper/pipelines.py) is:

```python
item.version = await self.document_service.next_version(item.identifier)
self._upload_document_content(item)
await self._insert_document_metadata(item)
```

The upload still finishes before metadata is inserted. A lock makes items with the same identifier wait for each other within this pipeline, preventing conflicting version choices.

The pipeline reuses its async MongoDB client throughout the crawl and closes it with `await self.mongo_client.close()`. Our existing `AsyncioSelectorReactor` setting already supports asyncio libraries. See the [Scrapy asyncio docs](https://docs.scrapy.org/en/2.14/topics/asyncio.html).

Only the pipeline's MongoDB operations became async. I intentionally kept MinIO uploads, spider ETag lookups, and transform reads synchronous and outside the scope of this change.
