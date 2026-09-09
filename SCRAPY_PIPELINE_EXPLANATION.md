# How items reach the Scrapy pipeline

Scrapy inspects each value yielded by the spider callback: a `Request` is scheduled for crawling, `None` is ignored, and any other value is sent through the item pipelines. It does not use the class name or pipeline type annotations to route items.

Our spider yields a `DocumentItem` dataclass. It reaches `DocumentPipeline.process_item()` because that pipeline is enabled in [settings.py](src/kedra_scraper/scraper/settings.py):

```python
ITEM_PIPELINES = {
    "kedra_scraper.scraper.pipelines.DocumentPipeline": 100,
}
```

In this repo, the flow is:

```text
SourceSpider yields DocumentItem → Scrapy → DocumentPipeline.process_item(item)
```

The pipeline stores new document content and metadata, then returns the item.

With multiple pipelines, every item visits them in ascending priority order. Each pipeline checks the item type itself and returns unrelated types unchanged:

```python
def process_item(self, item):
    if not isinstance(item, DocumentItem):
        return item

    # Process this DocumentItem.
    return item
```

Returning an item passes it onward. Raising `DropItem` stops all later pipelines from receiving it, so it should not be used just to skip an unrelated type.

See the [Scrapy item pipeline docs](https://docs.scrapy.org/en/latest/topics/item-pipeline.html), including [pipeline activation and ordering](https://docs.scrapy.org/en/latest/topics/item-pipeline.html#activating-an-item-pipeline-component).
