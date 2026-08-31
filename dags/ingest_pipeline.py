"""Ingest pipeline DAG — deliberately thin: every task
subprocesses a scraper/transform entrypoint script. Subprocess, not
CrawlerProcess: the Twisted reactor can't restart, so a second mapped task in
the same worker would die.

Scrape and transform are paired inside a mapped task group: each partition
transforms as soon as its own scrape finishes, instead of one global
transform waiting for every partition. Tasks take date params, not XCom —
resolving a mapped XCom list with failed members raises, defeating
``all_done``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta

from airflow.decorators import dag, task, task_group
from airflow.models.param import Param

from kedra_scraper.services import ConfigService, RunService
from kedra_scraper.utils.partitions import build_partitions as build_partition_windows

# How many partitions may scrape at once. Every partition hits the same
# domain, so this multiplies the per-process download_delay politeness rate.
PARALLEL_SCRAPES = 5
# Transforms are local work (Mongo + MinIO), no politeness concern — this
# only bounds CPU/memory on the worker.
PARALLEL_TRANSFORMS = 2


@dag(
    dag_id="ingest_pipeline",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    params={
        "source_id": Param("wrc", type="string"),
        "start_date": Param("2026-01-01", type="string"),
        "end_date": Param("2026-01-31", type="string"),
    },
)
def ingest_pipeline():
    @task
    def build_partitions(params: dict | None = None, dag_run=None) -> list[dict]:
        """Load the config once — a mid-run edit must not split
        the run."""

        params = params or {}
        missing = []
        for key in ("source_id", "start_date", "end_date"):
            if not params.get(key):
                missing.append(key)
        if missing:
            missing_parameter_names = ", ".join(missing)
            raise ValueError(
                "build_partitions: missing required params: "
                f"{missing_parameter_names}"
            )

        section_configs_by_id = ConfigService().get_sections_by_source_id(
            params["source_id"]
        )
        date_range_start = date.fromisoformat(params["start_date"])
        date_range_end = date.fromisoformat(params["end_date"])
        run_id = f"airflow-{dag_run.run_id}"

        partition_specs = []
        for section_id, section_config in section_configs_by_id.items():
            section_partitions = build_partition_windows(
                section_id,
                date_range_start,
                date_range_end,
                section_config.partition_days,
            )
            for partition in section_partitions:
                partition_specs.append(
                    {
                        "section_id": section_id,
                        "start": partition.start.isoformat(),
                        "end": partition.end.isoformat(),
                        "run_id": run_id,
                    }
                )
        return partition_specs

    @task(retries=2, retry_delay=timedelta(minutes=5),
          max_active_tis_per_dagrun=PARALLEL_SCRAPES)
    def scrape_partition(partition_spec: dict):
        completed_process = subprocess.run(
            [sys.executable, "-m", "kedra_scraper.scraper.runner",
             "--section", partition_spec["section_id"],
             "--start", partition_spec["start"],
             "--end", partition_spec["end"],
             "--run-id", partition_spec["run_id"]],
            capture_output=True, text=True,
        )
        command_output = (
            completed_process.stdout + "\n" + completed_process.stderr
        ).strip()
        print(command_output)
        if completed_process.returncode != 0:
            output_tail = "\n".join(command_output.splitlines()[-10:])
            raise RuntimeError(
                f"scraper.runner exited {completed_process.returncode} "
                f"for {partition_spec['section_id']} "
                f"{partition_spec['start']}..{partition_spec['end']}:\n"
                f"{output_tail}"
            )

    @task(trigger_rule="all_done", max_active_tis_per_dagrun=PARALLEL_TRANSFORMS)
    def transform_partition(partition_spec: dict):
        # all_done: a partially failed scrape may still have stored documents,
        # and transform's diff query picks up exactly what landed.
        subprocess.run(
            [sys.executable, "-m", "kedra_scraper.transform",
             "--section", partition_spec["section_id"],
             "--start", partition_spec["start"],
             "--end", partition_spec["end"],
             "--run-id", partition_spec["run_id"]],
            check=True,
        )

    @task_group
    def ingest_partition(partition_spec: dict):
        """One partition end to end: transform starts as soon as this
        partition's scrape is done, independent of the other partitions."""
        scrape_partition(partition_spec) >> transform_partition(partition_spec)

    @task(trigger_rule="all_done")
    def summarize(dag_run=None):
        run_id = f"airflow-{dag_run.run_id}"
        run_summary = RunService().get_run_by_id(run_id)
        if run_summary is None:
            print(f"no run summary for {run_id}")
            return
        print(json.dumps(run_summary, default=str, indent=2))

    ingest_partition.expand(partition_spec=build_partitions()) >> summarize()


ingest_pipeline()
