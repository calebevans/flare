"""Seed a log file into a LocalStack CloudWatch log group.

Usage:
    python scripts/seed_logs.py /aws/apache/server logs/apache_sample.log
    python scripts/seed_logs.py /test/app tests/fixtures/sample_logs.txt
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from typing import Any

import boto3


def seed(log_group: str, filepath: str) -> None:
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    client = boto3.client("logs", endpoint_url=endpoint)

    with contextlib.suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_log_group(logGroupName=log_group)

    stream = "stream-1"
    with contextlib.suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_log_stream(logGroupName=log_group, logStreamName=stream)

    with open(filepath) as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]

    now_ms = int(time.time() * 1000)
    batch: list[dict[str, object]] = []
    batch_bytes = 0
    token: str | None = None

    for i, line in enumerate(lines):
        event = {
            "timestamp": now_ms - (len(lines) - i) * 100,
            "message": line,
        }
        event_bytes = len(line.encode("utf-8")) + 26
        if len(batch) >= 10_000 or batch_bytes + event_bytes > 1_048_576:
            token = _put_batch(client, log_group, stream, batch, token)
            batch = []
            batch_bytes = 0
        batch.append(event)
        batch_bytes += event_bytes

    if batch:
        _put_batch(client, log_group, stream, batch, token)

    print(f"Seeded {len(lines)} log events into {log_group}")


def _put_batch(
    client: Any,
    log_group: str,
    stream: str,
    batch: list[dict[str, object]],
    token: str | None,
) -> str | None:
    kwargs: dict[str, object] = {
        "logGroupName": log_group,
        "logStreamName": stream,
        "logEvents": batch,
    }
    if token:
        kwargs["sequenceToken"] = token
    resp = client.put_log_events(**kwargs)
    return resp.get("nextSequenceToken")  # type: ignore[no-any-return]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <log-group-name> <log-file-path>")
        sys.exit(1)
    seed(sys.argv[1], sys.argv[2])
