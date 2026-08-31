"""Per-request rotating proxy middleware (off by default)."""

import logging
from itertools import cycle
from pathlib import Path

from scrapy.exceptions import NotConfigured

logger = logging.getLogger(__name__)

class RotatingProxyMiddleware:
    """Use the next configured proxy for every outgoing request."""

    def __init__(self, proxy_urls: list[str]):
        self.proxy_url_cycle = cycle(proxy_urls)

    # Scrapy factory hook, name fixed by Scrapy: builds the middleware, or
    # raising NotConfigured removes it from the chain.
    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool("PROXY_ENABLED"):
            raise NotConfigured
        proxy_list_path_value = crawler.settings.get("PROXY_LIST_PATH")
        if not proxy_list_path_value:
            raise NotConfigured("PROXY_LIST_PATH is not configured")
        proxy_list_path = Path(proxy_list_path_value)
        if not proxy_list_path.is_file():
            raise NotConfigured(
                f"PROXY_ENABLED is on but {proxy_list_path} does not exist"
            )
        proxy_urls = _load_proxy_urls(proxy_list_path)
        if not proxy_urls:
            raise NotConfigured(f"proxy list {proxy_list_path} is empty")
        return cls(proxy_urls)

    # Scrapy hook, name fixed by Scrapy: called for every outgoing request.
    # Scrapy 2.14 stopped passing the spider to downloader middleware hooks.
    def process_request(self, request):
        proxy_url = next(self.proxy_url_cycle)
        request.meta["proxy"] = proxy_url
        # Credential-free host:port, carried onto the Mongo row for debugging.
        proxy_host = proxy_url.rsplit("@", 1)[-1]
        request.meta["proxy_host"] = proxy_host
        logger.debug("proxy %s -> %s", proxy_host, request.url)

def _load_proxy_urls(proxy_list_path: Path) -> list[str]:
    """Parse ``host:port:username:password`` lines into proxy URLs of the
    form ``http://username:password@host:port``. Blank lines and ``#``
    comments are skipped."""
    proxy_urls = []
    for proxy_entry in proxy_list_path.read_text().splitlines():
        proxy_entry = proxy_entry.strip()
        if not proxy_entry or proxy_entry.startswith("#"):
            continue
        host, port, username, password = proxy_entry.split(":", 3)
        proxy_urls.append(f"http://{username}:{password}@{host}:{port}")
    return proxy_urls
