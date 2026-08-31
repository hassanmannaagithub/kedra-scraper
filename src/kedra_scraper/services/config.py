"""Config slice: the shapes read from ``config/sources.yaml`` plus the
loader that serves them. (Env-derived runtime settings live in
``kedra_scraper.config``; selectors and per-source behaviour live here.)

Everything returned to callers has passed pydantic validation — malformed
config raises at startup, not mid-crawl.
"""

from typing import Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kedra_scraper.config import get_settings

_LISTING_PARAM_DEFAULTS = {
    "date_format": "%d/%m/%Y",
    "date_from": "from",
    "date_to": "to",
    "page": "pageNumber",
}


class ConfigError(Exception):
    pass


class Selectors(BaseModel):
    """CSS selectors for one section. ``doc_link`` is optional: for sources
    where the decision *is* the detail page (all four WRC bodies),
    the detail URL doubles as the document URL. ``identifier`` may be a list:
    selectors are tried in order until one yields text (fallback chain for
    sites that render the ref inconsistently — e.g. WRC's empty refNO rows)."""

    model_config = ConfigDict(extra="forbid")

    listing_row: str
    empty_result: Optional[str] = None
    identifier: Union[str, list[str]]
    title: str
    description: str
    published_date: str
    detail_link: str
    next_page: str
    doc_link: Optional[str] = None
    content_container: str

class SectionConfig(BaseModel):
    """One searchable body on a source, as declared in ``config/sources.yaml``."""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    source_id: str
    url: str
    selectors: Selectors
    partition_days: int = Field(
        default=30, ge=1, description="days per partition"
    )


class SourceConfig(BaseModel):
    """One website, as declared in ``config/sources.yaml``."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    base_url: str
    # Query-param names/format for the listing URL. A dict, not dedicated
    # fields: a site needing one more param (e.g. category) just adds a key
    # here instead of the schema growing a new column for it.
    listing_params: dict[str, str] = Field(
        default_factory=lambda: dict(_LISTING_PARAM_DEFAULTS)
    )
    user_agent: str
    download_delay: float
    concurrent_requests: int
    http_timeout: int
    robots_obey: bool
    autothrottle_enabled: bool
    autothrottle_start_delay: float
    autothrottle_max_delay: float
    retry_enabled: bool
    retry_times: int
    retry_http_codes: list[int]
    proxy_enabled: bool
    proxy_list_path: str

class ConfigService:
    """Loads ``config/sources.yaml`` once and serves it. Interfaces
    (scrape/transform entrypoints, DAG) read config through this class."""

    def __init__(self):
        self.config_path = get_settings().config_path
        self.sources, self.sections = self._load(self.config_path)

    @staticmethod
    def _load(
        config_path: str,
    ) -> tuple[dict[str, SourceConfig], dict[str, SectionConfig]]:
        with open(config_path) as config_file:
            raw_config = yaml.safe_load(config_file) or {}

        source_configs: dict[str, SourceConfig] = {}
        section_configs: dict[str, SectionConfig] = {}
        for source_id, source_data in (raw_config.get("sources") or {}).items():
            source_data = dict(source_data)
            section_data_by_id = source_data.pop("sections", {})
            source_configs[source_id] = SourceConfig.model_validate(
                {**source_data, "source_id": source_id}
            )
            for section_id, section_data in section_data_by_id.items():
                section_configs[section_id] = SectionConfig.model_validate(
                    {
                        **section_data,
                        "section_id": section_id,
                        "source_id": source_id,
                    }
                )
        return source_configs, section_configs

    def get_source(self, source_id: str) -> SourceConfig:
        source_config = self.sources.get(source_id)
        if source_config is None:
            raise ConfigError(f"unknown source {source_id!r}")
        return source_config

    def get_section(self, section_id: str) -> SectionConfig:
        section_config = self.sections.get(section_id)
        if section_config is None:
            raise ConfigError(f"unknown section {section_id!r}")
        return section_config

    def get_sections_by_source_id(self, source_id: str) -> dict[str, SectionConfig]:
        """Config for every section of a source, resolved once at run start.
        A mid-run edit must not split a run."""
        self.get_source(source_id)  # raises if the source itself is unknown
        matching_sections: dict[str, SectionConfig] = {}
        for section_id in sorted(self.sections):
            section_config = self.sections[section_id]
            if section_config.source_id == source_id:
                matching_sections[section_id] = section_config
        return matching_sections
