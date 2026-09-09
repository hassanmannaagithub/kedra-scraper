# Making MongoDB operations async

The issue was that synchronous PyMongo calls blocked Scrapy's event loop while waiting for MongoDB. Simply changing `process_item()` to `async def` would not fix those blocking database calls.

I replaced `MongoClient` with PyMongo's `AsyncMongoClient`, made the functions performing database operations async, and awaited their calls. I applied this throughout the application: the pipeline, spider ETag lookups, transforms, indexes, and run summaries. Cursor results are consumed with `async for`, and clients are closed asynchronously after use.

Scrapy can now continue other work while MongoDB requests are pending. Its existing `AsyncioSelectorReactor` setting supports this. MinIO operations remain synchronous.

References I used:

- [Scrapy item pipelines](https://docs.scrapy.org/en/2.14/topics/item-pipeline.html): support for `async def process_item()`.
- [PyMongo async migration guide](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/#migrate-from-pymongo): replacing the client, awaiting operations, and using async cursors.
- [Scrapy asyncio support](https://docs.scrapy.org/en/2.14/topics/asyncio.html): the asyncio reactor configuration.
