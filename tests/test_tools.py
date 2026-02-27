from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws

from flare.tools import check_resource_status, query_logs, query_metrics


@pytest.fixture()
def _cloudwatch_client():
    with mock_aws():
        client = boto3.client("cloudwatch", region_name="us-east-1")
        client.put_metric_data(
            Namespace="AWS/RDS",
            MetricData=[
                {
                    "MetricName": "DatabaseConnections",
                    "Dimensions": [
                        {"Name": "DBInstanceIdentifier", "Value": "auth-db"}
                    ],
                    "Timestamp": datetime.now(tz=UTC),
                    "Value": 95.0,
                    "Unit": "Count",
                }
            ],
        )
        yield client


@pytest.fixture()
def _logs_client():
    with mock_aws():
        client = boto3.client("logs", region_name="us-east-1")
        client.create_log_group(logGroupName="/test/log-group")
        client.create_log_stream(
            logGroupName="/test/log-group", logStreamName="test-stream"
        )
        client.put_log_events(
            logGroupName="/test/log-group",
            logStreamName="test-stream",
            logEvents=[
                {
                    "timestamp": int(datetime.now(tz=UTC).timestamp() * 1000),
                    "message": "ERROR Connection refused",
                },
                {
                    "timestamp": int(datetime.now(tz=UTC).timestamp() * 1000),
                    "message": "INFO Request processed",
                },
            ],
        )
        yield client


def test_query_metrics(_cloudwatch_client):
    result = query_metrics(
        namespace="AWS/RDS",
        metric_name="DatabaseConnections",
        dimensions={"DBInstanceIdentifier": "auth-db"},
        period_minutes=60,
        stat="Average",
        cloudwatch_client=_cloudwatch_client,
    )
    assert result["namespace"] == "AWS/RDS"
    assert result["metric_name"] == "DatabaseConnections"
    assert "error" not in result


def test_query_metrics_error():
    mock_client = Mock()
    mock_client.get_metric_statistics.side_effect = Exception("API error")

    result = query_metrics(
        namespace="AWS/RDS",
        metric_name="BadMetric",
        dimensions={},
        cloudwatch_client=mock_client,
    )
    assert "error" in result


def test_query_logs(_logs_client):
    result = query_logs(
        log_group="/test/log-group",
        filter_pattern="ERROR",
        lookback_minutes=60,
        logs_client=_logs_client,
    )
    assert result["log_group"] == "/test/log-group"
    assert "error" not in result


def test_query_logs_error():
    mock_client = Mock()
    mock_client.filter_log_events.side_effect = Exception("API error")

    result = query_logs(
        log_group="/nonexistent",
        logs_client=mock_client,
    )
    assert "error" in result


def test_check_resource_status_unsupported():
    result = check_resource_status("unknown_type", "some-id")
    assert "error" in result
    assert "Unsupported" in result["error"]
