"""Process entrypoint: ``python -m kedra_scraper.transform``.
Airflow subprocesses this per partition range, kept separate from
``service.py`` to preserve the interface/domain split."""

import argparse
import json
import sys
from datetime import date

from kedra_scraper.config import get_settings
from kedra_scraper.transform.service import TransformService
from kedra_scraper.utils.db import ensure_indexes, get_databases
from kedra_scraper.utils.log import setup_logging


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform stored documents for a partition-date range."
    )
    parser.add_argument("--start", required=True, help="Range start, ISO date.")
    parser.add_argument("--end", required=True, help="Range end, ISO date (inclusive).")
    parser.add_argument(
        "--section",
        required=True,
        help="Section to transform.",
    )
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    setup_logging(get_settings().log_level)
    ensure_indexes(get_databases())

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    document_counts = TransformService().run(
        start,
        end,
        args.run_id,
        section_id=args.section,
    )
    print(json.dumps({"run_id": args.run_id, **document_counts}))
    if document_counts["failed"]:
        sys.exit(2)


if __name__ == "__main__":
    _main()
