from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from flare.handler import handler


def _setup_env(monkeypatch: Any, sns_arn: str) -> None:
    monkeypatch.setenv("LOG_GROUP_PATTERNS", "/test/app")
    monkeypatch.setenv("SNS_TOPIC_ARN", sns_arn)
    monkeypatch.setenv("LOOKBACK_MINUTES", "5")
    monkeypatch.setenv("TOKEN_BUDGET", "100000")


def _seed_logs(client: Any, group: str, messages: list[str]) -> None:
    client.create_log_group(logGroupName=group)
    client.create_log_stream(logGroupName=group, logStreamName="stream-1")
    now_ms = int(time.time() * 1000)
    events = [
        {"timestamp": now_ms - (len(messages) - i) * 1000, "message": msg}
        for i, msg in enumerate(messages)
    ]
    client.put_log_events(
        logGroupName=group, logStreamName="stream-1", logEvents=events
    )


def _make_client_dispatcher(logs_client: Any, sns_client: Any) -> Any:
    def _dispatcher(service: str, **kwargs: Any) -> Any:
        if service == "logs":
            return logs_client
        if service == "sns":
            return sns_client
        raise ValueError(f"Unexpected service: {service}")

    return _dispatcher


@mock_aws
class TestHandlerIntegration:
    @patch("flare.handler.resolve_log_groups", return_value=["/test/app"])
    @patch("flare.handler.analyze_logs")
    @patch("litellm.completion")
    def test_schedule_event_full_pipeline(
        self,
        mock_completion: MagicMock,
        mock_analyze: MagicMock,
        _mock_resolve: MagicMock,
        schedule_event: dict,
        nova_response: str,
        cordon_output: str,
        monkeypatch: Any,
    ) -> None:
        logs_client = boto3.client("logs", region_name="us-east-1")
        sns_client = boto3.client("sns", region_name="us-east-1")
        topic = sns_client.create_topic(Name="test-topic")
        sns_arn = topic["TopicArn"]

        _setup_env(monkeypatch, sns_arn)
        _seed_logs(
            logs_client,
            "/test/app",
            [
                "INFO Processing batch 0",
                "INFO Processing batch 1",
                "ERROR Connection refused",
            ],
        )

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]
        mock_completion.return_value = mock_resp
        mock_analyze.return_value = cordon_output

        dispatcher = _make_client_dispatcher(logs_client, sns_client)
        with (
            patch("flare.logs.boto3.client", side_effect=dispatcher),
            patch("flare.notifier.boto3.client", side_effect=dispatcher),
        ):
            result = handler(schedule_event, None)

        assert result["statusCode"] == 200

    @patch("flare.handler.resolve_log_groups", return_value=["/test/app"])
    @patch("litellm.completion")
    def test_returns_no_logs_when_group_empty(
        self,
        mock_completion: MagicMock,
        _mock_resolve: MagicMock,
        schedule_event: dict,
        monkeypatch: Any,
    ) -> None:
        logs_client = boto3.client("logs", region_name="us-east-1")
        sns_client = boto3.client("sns", region_name="us-east-1")
        topic = sns_client.create_topic(Name="test-topic")
        sns_arn = topic["TopicArn"]

        _setup_env(monkeypatch, sns_arn)
        logs_client.create_log_group(logGroupName="/test/app")
        logs_client.create_log_stream(
            logGroupName="/test/app", logStreamName="stream-1"
        )

        with patch("flare.logs.boto3.client", return_value=logs_client):
            result = handler(schedule_event, None)

        assert result["body"] == "No logs found"
        mock_completion.assert_not_called()

    @patch(
        "flare.handler.resolve_log_groups",
        return_value=["/aws/lambda/my-app"],
    )
    @patch("flare.handler.analyze_logs")
    @patch("litellm.completion")
    def test_subscription_event_uses_raw_logs(
        self,
        mock_completion: MagicMock,
        mock_analyze: MagicMock,
        _mock_resolve: MagicMock,
        subscription_event: dict,
        nova_response: str,
        monkeypatch: Any,
    ) -> None:
        sns_client = boto3.client("sns", region_name="us-east-1")
        topic = sns_client.create_topic(Name="test-topic")
        sns_arn = topic["TopicArn"]

        monkeypatch.setenv("LOG_GROUP_PATTERNS", "/aws/lambda/my-app")
        monkeypatch.setenv("SNS_TOPIC_ARN", sns_arn)
        monkeypatch.setenv("TOKEN_BUDGET", "100000")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]
        mock_completion.return_value = mock_resp

        with patch("flare.notifier.boto3.client", return_value=sns_client):
            result = handler(subscription_event, None)

        assert result["statusCode"] == 200
