# syntax=docker/dockerfile:1

FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/kedra

COPY pyproject.toml scrapy.cfg ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config ./config

FROM app AS test

RUN pip install --no-cache-dir ".[dev]"
COPY tests ./tests

CMD ["pytest"]

FROM apache/airflow:2.10.4-python3.12 AS airflow

COPY --chown=airflow:root pyproject.toml scrapy.cfg /opt/ingest/
COPY --chown=airflow:root src /opt/ingest/src

USER airflow
RUN pip install --no-cache-dir /opt/ingest
