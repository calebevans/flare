from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from mypy_boto3_logs import CloudWatchLogsClient


def resolve_log_groups(
    patterns: list[str],
    *,
    logs_client: CloudWatchLogsClient | None = None,
) -> list[str]:
    if logs_client is None:
        logs_client = boto3.client("logs")

    resolved: set[str] = set()
    for pattern in patterns:
        if pattern.endswith("*"):
            prefix = pattern.rstrip("*").rstrip("/")
            next_token: str | None = None
            while True:
                if next_token:
                    resp = logs_client.describe_log_groups(
                        logGroupNamePrefix=prefix, nextToken=next_token
                    )
                else:
                    resp = logs_client.describe_log_groups(logGroupNamePrefix=prefix)
                for group in resp.get("logGroups", []):
                    name = group.get("logGroupName", "")
                    if name:
                        resolved.add(name)
                next_token = resp.get("nextToken")
                if not next_token:
                    break
        else:
            resolved.add(pattern)

    return sorted(resolved)


def fetch_logs(
    log_group: str,
    lookback_minutes: int,
    *,
    logs_client: CloudWatchLogsClient | None = None,
) -> str:
    if logs_client is None:
        logs_client = boto3.client("logs")

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (lookback_minutes * 60 * 1000)

    lines: list[str] = []
    kwargs: dict[str, object] = {
        "logGroupName": log_group,
        "startTime": start_ms,
        "endTime": end_ms,
        "interleaved": True,
    }

    while True:
        response = logs_client.filter_log_events(**kwargs)  # type: ignore[arg-type]
        for event in response.get("events", []):
            ts = event.get("timestamp", 0)
            msg = event.get("message", "").rstrip("\n")
            dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
            lines.append(f"{dt.isoformat()} {msg}")

        next_token = response.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token

    return "\n".join(lines)
