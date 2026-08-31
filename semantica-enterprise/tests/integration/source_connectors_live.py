#!/usr/bin/env python3
"""Real protocol checks for account-free and local Docker data sources."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import pika
from minio import Minio

from packages.semantica_adapter.ingest import ingest_source
from tests.fixtures.generate_multimodal import generate


def check(name: str, source_type: str, config: dict, secret: str | None = None) -> dict:
    payload = ingest_source(
        source_type=source_type,
        source_name=f"live-{name}",
        config=config,
        secret=secret,
    )
    assert payload.body, name
    assert payload.filename, name
    return {"bytes": len(payload.body), "content_type": payload.content_type, **payload.metadata}


def main() -> None:
    results = {
        "web": check("web", "web", {"url": "http://source-fixture:8088/page", "respect_robots": True}),
        "rest": check("rest", "rest", {"url": "http://source-fixture:8088/api", "method": "GET"}),
        "rss": check("rss", "rss", {"url": "http://source-fixture:8088/feed.xml", "max_items": 10}),
        "sitemap": check("sitemap", "sitemap", {"url": "http://source-fixture:8088/sitemap.xml", "max_urls": 5, "respect_robots": True}),
        "webdav": check("webdav", "webdav", {"url": "http://source-fixture:8088/dav/"}),
        "database": check(
            "postgresql",
            "database",
            {
                "dialect": "postgresql",
                "host": "postgres",
                "port": 5432,
                "database": "semantica",
                "username": "semantica",
                "include_tables": ["schema_migrations"],
                "max_rows_per_table": 20,
            },
            "semantica",
        ),
    }

    source_root = Path("/app/data/sources/live-connector-fixture")
    try:
        fixtures = generate(source_root)
        results["local_dir"] = check("local", "local_dir", {"path": str(source_root), "max_files": 100})
        for source_type, filename in (("parquet", "fact.parquet"), ("arrow", "fact.arrow"), ("duckdb", "fact.csv")):
            if filename in fixtures:
                results[source_type] = check(source_type, source_type, {"path": str(fixtures[filename]), "limit": 10})
        results["huggingface"] = check(
            "huggingface-local",
            "huggingface",
            {"dataset": "json", "split": "train", "data_files": [str(fixtures["fact.jsonl"])], "limit": 10},
        )
    finally:
        shutil.rmtree(source_root, ignore_errors=True)

    minio = Minio("minio:9000", access_key="semantica", secret_key="semantica-dev-secret", secure=False)
    bucket, object_name = "chuanshen-source-fixture", "knowledge/fact.txt"
    try:
        if not minio.bucket_exists(bucket):
            minio.make_bucket(bucket)
        from io import BytesIO
        body = b"NexusOne S3 fixture fact"
        minio.put_object(bucket, object_name, BytesIO(body), len(body), content_type="text/plain")
        results["s3"] = check(
            "s3",
            "s3",
            {"endpoint": "minio:9000", "bucket": bucket, "prefix": "knowledge/", "access_key": "semantica"},
            "semantica-dev-secret",
        )
    finally:
        try:
            minio.remove_object(bucket, object_name)
            minio.remove_bucket(bucket)
        except Exception:
            pass

    credentials = pika.PlainCredentials("semantica", "semantica")
    connection = pika.BlockingConnection(pika.ConnectionParameters("rabbitmq", 5672, "/", credentials))
    channel = connection.channel()
    queue = "chuanshen-source-fixture"
    channel.queue_declare(queue=queue, durable=True)
    channel.basic_publish("", queue, json.dumps({"product": "NexusOne", "year": 2026}).encode())
    connection.close()
    results["stream"] = check(
        "stream",
        "stream",
        {"stream_type": "rabbitmq", "host": "rabbitmq", "port": 5672, "username": "semantica", "queue": queue, "max_messages": 10},
        "semantica",
    )

    indexes = httpx.get("http://opensearch:9200/_cat/indices?format=json", timeout=10).json()
    candidate = next((row["index"] for row in indexes if not row["index"].startswith(".")), None)
    if candidate:
        results["opensearch"] = check(
            "opensearch",
            "opensearch",
            {"url": "http://opensearch:9200", "index": candidate, "limit": 5, "verify_certs": False},
        )

    print(json.dumps({"connectors": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
