from __future__ import annotations

import time

import boto3
from moto import mock_aws

from flare.logs import fetch_logs, resolve_log_groups


@mock_aws
class TestResolveLogGroups:
    def test_exact_names_pass_through(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/app/web")

        result = resolve_log_groups(["/app/web"], logs_client=client)

        assert result == ["/app/web"]

    def test_prefix_pattern_matches(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/aws/lambda/func-a")
        client.create_log_group(logGroupName="/aws/lambda/func-b")
        client.create_log_group(logGroupName="/aws/ecs/service-x")

        result = resolve_log_groups(["/aws/lambda/*"], logs_client=client)

        assert result == ["/aws/lambda/func-a", "/aws/lambda/func-b"]

    def test_mixed_exact_and_pattern(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/aws/lambda/func-a")
        client.create_log_group(logGroupName="/my-app/api")

        result = resolve_log_groups(
            ["/aws/lambda/*", "/my-app/api"], logs_client=client
        )

        assert "/aws/lambda/func-a" in result
        assert "/my-app/api" in result

    def test_pattern_with_no_matches_returns_empty(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")

        result = resolve_log_groups(["/nonexistent/*"], logs_client=client)

        assert result == []

    def test_deduplicates(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/aws/lambda/func-a")

        result = resolve_log_groups(
            ["/aws/lambda/*", "/aws/lambda/func-a"], logs_client=client
        )

        assert result == ["/aws/lambda/func-a"]


@mock_aws
class TestFetchLogs:
    def _seed_log_group(self, client: object, group: str, messages: list[str]) -> None:
        client.create_log_group(logGroupName=group)  # type: ignore[union-attr]
        client.create_log_stream(  # type: ignore[union-attr]
            logGroupName=group, logStreamName="stream-1"
        )
        now_ms = int(time.time() * 1000)
        events = [
            {"timestamp": now_ms - (len(messages) - i) * 1000, "message": msg}
            for i, msg in enumerate(messages)
        ]
        client.put_log_events(  # type: ignore[union-attr]
            logGroupName=group,
            logStreamName="stream-1",
            logEvents=events,
        )

    def test_returns_log_lines(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        self._seed_log_group(
            client,
            "/test/app",
            ["INFO Starting up", "ERROR Connection failed", "INFO Retrying"],
        )

        result = fetch_logs("/test/app", lookback_minutes=5, logs_client=client)

        assert "INFO Starting up" in result
        assert "ERROR Connection failed" in result
        assert "INFO Retrying" in result

    def test_returns_lines_with_timestamps(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        self._seed_log_group(client, "/test/app", ["test message"])

        result = fetch_logs("/test/app", lookback_minutes=5, logs_client=client)
        lines = result.strip().split("\n")

        # Each line should start with an ISO timestamp
        assert lines[0][0:4] == "2026" or lines[0][0:4] == "2025"
        assert "test message" in lines[0]

    def test_empty_log_group(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/test/empty")
        client.create_log_stream(logGroupName="/test/empty", logStreamName="stream-1")

        result = fetch_logs("/test/empty", lookback_minutes=5, logs_client=client)

        assert result == ""

    def test_respects_lookback_window(self) -> None:
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/test/app")
        client.create_log_stream(logGroupName="/test/app", logStreamName="stream-1")

        now_ms = int(time.time() * 1000)
        # One event from 2 minutes ago, one from 10 minutes ago
        client.put_log_events(
            logGroupName="/test/app",
            logStreamName="stream-1",
            logEvents=[
                {"timestamp": now_ms - 10 * 60 * 1000, "message": "old message"},
                {"timestamp": now_ms - 2 * 60 * 1000, "message": "recent message"},
            ],
        )

        result = fetch_logs("/test/app", lookback_minutes=5, logs_client=client)

        assert "recent message" in result
        assert "old message" not in result
