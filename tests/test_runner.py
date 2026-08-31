from datetime import date
from types import SimpleNamespace

import kedra_scraper.scraper.runner as runner_module


class FakeScrapySettings:
    def __init__(self):
        self.values = {}

    def set(self, name, value, priority):
        assert priority == "cmdline"
        self.values[name] = value


class FakeCrawlerProcess:
    def __init__(self, settings, install_root_handler):
        self.settings = settings
        self.install_root_handler = install_root_handler
        self.crawler = SimpleNamespace(
            stats=SimpleNamespace(get_stats=lambda: {"finish_reason": "finished"})
        )

    def create_crawler(self, _spider_class):
        return self.crawler

    def crawl(self, _crawler, **_arguments):
        pass

    def start(self):
        pass


def test_run_crawl_applies_source_scraping_settings(monkeypatch):
    scrapy_settings = FakeScrapySettings()
    source_config = SimpleNamespace(
        user_agent="test-agent",
        robots_obey=False,
        download_delay=2.5,
        concurrent_requests=4,
        http_timeout=45,
        autothrottle_enabled=True,
        autothrottle_start_delay=0.5,
        autothrottle_max_delay=20.0,
        retry_enabled=True,
        retry_times=5,
        retry_http_codes=[429, 500],
        proxy_enabled=False,
        proxy_list_path="test-proxies.txt",
    )
    section_config = SimpleNamespace(section_id="test-section")

    monkeypatch.setattr(
        runner_module,
        "get_project_settings",
        lambda: scrapy_settings,
    )
    monkeypatch.setattr(
        runner_module,
        "CrawlerProcess",
        FakeCrawlerProcess,
    )

    runner_module._run_crawl(
        section_config=section_config,
        source_config=source_config,
        run_id="run-1",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 31),
    )

    assert scrapy_settings.values == {
        "USER_AGENT": "test-agent",
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 2.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_TIMEOUT": 45,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 0.5,
        "AUTOTHROTTLE_MAX_DELAY": 20.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 4.0,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 5,
        "RETRY_HTTP_CODES": [429, 500],
        "PROXY_ENABLED": False,
        "PROXY_LIST_PATH": "test-proxies.txt",
    }
