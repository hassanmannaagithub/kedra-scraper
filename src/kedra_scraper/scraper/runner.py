"""Run one (section, date-window) partition in this process.

This is scrapy process bootstrap, not domain logic — hence it lives with the
scraper, not in ``services/``. Writes go through the entity services: the
item pipeline inserts documents via ``DocumentService``; the partition
summary is recorded here via ``RunService``.

One crawler per invocation — the Twisted reactor cannot restart, which is why
Airflow subprocesses ``python -m kedra_scraper.scraper.runner`` instead of
calling this in-process repeatedly.
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from kedra_scraper.config import get_settings
from kedra_scraper.scraper.spiders.source_spider import SourceSpider
from kedra_scraper.services.config import (ConfigError, ConfigService,
                                           SectionConfig, SourceConfig)
from kedra_scraper.services.run import RunService
from kedra_scraper.utils.db import ensure_indexes, get_databases
from kedra_scraper.utils.log import get_logger, setup_logging

_DOCUMENT_COUNT_METRICS = ("found", "scraped", "failed", "skipped_unchanged")


def run_partition(section_id: str, window_start: date, window_end: date,
                  run_id: str) -> dict:
    """Returns {run_id, finish_reason, fatal, document_counts_by_status}.
    Raises ConfigError for config problems (before any request goes out)."""
    if window_end < window_start:
        raise ConfigError(f"end {window_end} before start {window_start}")

    section_config, source_config = _resolve_config(section_id)

    logger = get_logger(
        "kedra.scrape",
        run_id=run_id,
        section_id=section_id,
        partition_date=window_start.isoformat(),
    )

    logger.info(
        "partition start",
        window_end=window_end.isoformat(),
        source_id=source_config.source_id,
    )
    started_at = datetime.now(timezone.utc)
    crawl_stats = _run_crawl(
        section_config,
        source_config,
        run_id,
        window_start,
        window_end,
    )
    finished_at = datetime.now(timezone.utc)

    finish_reason = crawl_stats.get("finish_reason", "unknown")

    document_counts_by_metric = {}
    for metric in _DOCUMENT_COUNT_METRICS:
        document_counts_by_metric[metric] = crawl_stats.get(f"kedra/{metric}", 0)

    if finish_reason == "finished":
        fatal = None
    else:
        fatal = str(crawl_stats.get("kedra/fatal", finish_reason))

    RunService().record_partition(
        run_id=run_id, source_id=source_config.source_id,
        section_id=section_config.section_id,
        window=(window_start, window_end),
        document_counts_by_status=document_counts_by_metric,
        finish_reason=finish_reason, fatal=fatal,
        started_at=started_at, finished_at=finished_at,
    )
    logger.info(
        "partition end",
        finish_reason=finish_reason,
        **document_counts_by_metric,
    )
    return {"run_id": run_id, "finish_reason": finish_reason,
            "fatal": fatal,
            "document_counts_by_status": document_counts_by_metric}


def _resolve_config(section_id: str) -> tuple[SectionConfig, SourceConfig]:
    """Load the config up front: malformed config fails here,
    never mid-crawl."""
    config_service = ConfigService()
    section_config = config_service.get_section(section_id)
    source_config = config_service.get_source(section_config.source_id)
    return section_config, source_config


def _run_crawl(
    section_config: SectionConfig,
    source_config: SourceConfig,
    run_id: str,
    window_start: date,
    window_end: date,
) -> dict:
    """One CrawlerProcess, one spider run; returns the crawler stats.

    A failed partition is re-run from scratch (Airflow retries); the dedup
    checks skip everything already stored, so no mid-crawl resume state is
    kept."""
    os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "kedra_scraper.scraper.settings")
    scrapy_settings = get_project_settings()

    source_scrapy_settings = {
        "USER_AGENT": source_config.user_agent,
        "ROBOTSTXT_OBEY": source_config.robots_obey,
        "DOWNLOAD_DELAY": source_config.download_delay,
        "CONCURRENT_REQUESTS_PER_DOMAIN": source_config.concurrent_requests,
        "DOWNLOAD_TIMEOUT": source_config.http_timeout,
        "AUTOTHROTTLE_ENABLED": source_config.autothrottle_enabled,
        "AUTOTHROTTLE_START_DELAY": source_config.autothrottle_start_delay,
        "AUTOTHROTTLE_MAX_DELAY": source_config.autothrottle_max_delay,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": float(
            source_config.concurrent_requests
        ),
        "RETRY_ENABLED": source_config.retry_enabled,
        "RETRY_TIMES": source_config.retry_times,
        "RETRY_HTTP_CODES": source_config.retry_http_codes,
        "PROXY_ENABLED": source_config.proxy_enabled,
        "PROXY_LIST_PATH": source_config.proxy_list_path,
    }
    for setting_name, setting_value in source_scrapy_settings.items():
        scrapy_settings.set(
            setting_name,
            setting_value,
            priority="cmdline",
        )

    crawler_process = CrawlerProcess(scrapy_settings, install_root_handler=False)
    crawler = crawler_process.create_crawler(SourceSpider)
    crawler_process.crawl(
        crawler,
        section=section_config.section_id,
        run_id=run_id,
        start=window_start.isoformat(),
        end=window_end.isoformat(),
    )
    crawler_process.start()
    return crawler.stats.get_stats()


def _main() -> None:
    """Process entrypoint: ``python -m kedra_scraper.scraper.runner``. Airflow
    subprocesses this per partition — see this module's docstring."""
    argument_parser = argparse.ArgumentParser(
        description="Scrape one (section, date-window) partition."
    )
    argument_parser.add_argument("--section", required=True)
    argument_parser.add_argument("--start", required=True, help="Window start, ISO date.")
    argument_parser.add_argument(
        "--end",
        required=True,
        help="Window end, ISO date (inclusive).",
    )
    argument_parser.add_argument("--run-id", required=True)
    arguments = argument_parser.parse_args()

    setup_logging(get_settings().log_level)
    ensure_indexes(get_databases())

    partition_result = run_partition(
        arguments.section,
        date.fromisoformat(arguments.start),
        date.fromisoformat(arguments.end),
        run_id=arguments.run_id,
    )
    if partition_result["finish_reason"] != "finished":
        print(
            "partition failed "
            f"({partition_result['finish_reason']}): {partition_result['fatal']}",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    _main()
