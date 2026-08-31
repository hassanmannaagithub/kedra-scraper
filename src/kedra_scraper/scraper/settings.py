"""Fixed Scrapy wiring.

Source-specific crawl values are loaded from ``config/sources.yaml`` and
injected by the runner before the crawler starts.
"""

from kedra_scraper.config import get_settings

BOT_NAME = "kedra_scraper"
SPIDER_MODULES = ["kedra_scraper.scraper.spiders"]
LOG_LEVEL = get_settings().log_level

DOWNLOADER_MIDDLEWARES = {
    "kedra_scraper.scraper.middlewares.RotatingProxyMiddleware": 610,
}
ITEM_PIPELINES = {
    "kedra_scraper.scraper.pipelines.DocumentPipeline": 100,
}

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

TELNETCONSOLE_ENABLED = False
