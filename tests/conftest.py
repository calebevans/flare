from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from flare.config import FlareConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def flare_config() -> FlareConfig:
    return FlareConfig(
        log_group_patterns=["/test/log-group"],
        sns_topic_arn="arn:aws:sns:us-east-1:123456789012:test-topic",
        lookback_minutes=30,
        bedrock_region="us-east-1",
        cordon_window_size=4,
        cordon_k_neighbors=5,
        nova_model_id="us.amazon.nova-2-lite-v1:0",
        token_budget=100_000,
        max_output_tokens=4096,
    )


@pytest.fixture()
def voice_config() -> FlareConfig:
    return FlareConfig(
        log_group_patterns=["/test/log-group"],
        sns_topic_arn="arn:aws:sns:us-east-1:123456789012:test-topic",
        nova_model_id="us.amazon.nova-2-lite-v1:0",
        token_budget=100_000,
        max_output_tokens=4096,
        connect_enabled=True,
        connect_instance_id="test-instance-id",
        connect_contact_flow_id="test-flow-id",
        connect_phone_number="+15551234567",
        oncall_phone="+15559876543",
        incidents_table_name="flare-incidents-test",
    )


@pytest.fixture()
def sample_incident() -> dict[str, Any]:
    return {
        "incident_id": "test-incident-123",
        "rca": (
            "STATUS: High\nSUMMARY: Connection pool exhaustion on auth-db.\n"
            "AFFECTED COMPONENTS: auth-service, api-gateway\n"
            "EVIDENCE:\n- Connection refused errors\n"
            "NEXT STEPS:\n1. Check RDS connections\n2. Restart auth-service"
        ),
        "alarm_name": "HighErrorRate-APIGateway",
        "alarm_reason": "Threshold crossed: ErrorRate > 10%",
        "trigger_type": "alarm",
        "timestamp": "2026-02-26T12:00:00+00:00",
        "prefetch_status": "complete",
        "cached_data": {
            "metrics": [
                {
                    "query_key": "RDS DatabaseConnections for auth-db",
                    "namespace": "AWS/RDS",
                    "metric_name": "DatabaseConnections",
                    "dimensions": {"DBInstanceIdentifier": "auth-db"},
                    "stat": "Average",
                    "datapoints": [
                        {
                            "timestamp": "2026-02-26T11:50:00+00:00",
                            "value": 95,
                            "unit": "Count",
                        },
                        {
                            "timestamp": "2026-02-26T11:55:00+00:00",
                            "value": 98,
                            "unit": "Count",
                        },
                    ],
                }
            ],
            "logs": [
                {
                    "query_key": "auth-service errors",
                    "log_group": "/aws/lambda/auth-service",
                    "filter_pattern": "ERROR",
                    "event_count": 15,
                    "sample_lines": [
                        "2026-02-26T11:52:00+00:00 ERROR "
                        "Connection refused from db pool",
                        "2026-02-26T11:53:00+00:00 ERROR "
                        "Connection pool exhausted",
                    ],
                }
            ],
            "status": [],
        },
    }


@pytest.fixture()
def prefetch_plan() -> dict[str, Any]:
    return {
        "metrics": [
            {
                "query_key": "RDS DatabaseConnections for auth-db",
                "namespace": "AWS/RDS",
                "metric_name": "DatabaseConnections",
                "dimensions": {"DBInstanceIdentifier": "auth-db"},
                "stat": "Average",
                "period_minutes": 60,
            }
        ],
        "log_queries": [
            {
                "query_key": "auth-service errors",
                "log_group": "/aws/lambda/auth-service",
                "filter_pattern": "ERROR",
                "lookback_minutes": 60,
            }
        ],
        "status_checks": [],
    }


@pytest.fixture()
def lex_fulfillment_event() -> dict[str, Any]:
    return {
        "sessionState": {
            "intent": {
                "name": "CheckMetrics",
                "state": "InProgress",
                "slots": {
                    "metric": {"value": {"interpretedValue": "connections"}},
                    "resource": {"value": {"interpretedValue": "auth-db"}},
                },
            },
            "sessionAttributes": {"incident_id": "test-incident-123"},
        },
        "inputTranscript": "Can you check the database connections for auth-db?",
    }


@pytest.fixture()
def connect_briefing_event() -> dict[str, Any]:
    return {
        "Details": {
            "ContactData": {
                "Attributes": {"incident_id": "test-incident-123"},
                "ContactId": "contact-abc-123",
            }
        }
    }


@pytest.fixture()
def sample_logs() -> str:
    return (FIXTURES / "sample_logs.txt").read_text()


@pytest.fixture()
def cordon_output() -> str:
    return (FIXTURES / "cordon_output.xml").read_text()


@pytest.fixture()
def nova_response() -> str:
    return (FIXTURES / "nova_response.txt").read_text()


@pytest.fixture()
def alarm_event() -> dict:
    return json.loads((FIXTURES / "alarm_event.json").read_text())


@pytest.fixture()
def schedule_event() -> dict:
    return json.loads((FIXTURES / "schedule_event.json").read_text())


@pytest.fixture()
def subscription_event() -> dict:
    return json.loads((FIXTURES / "subscription_event.json").read_text())


@pytest.fixture()
def mock_litellm_completion(mocker: Mock, nova_response: str) -> Mock:
    mock_resp = Mock()
    mock_resp.choices = [Mock(message=Mock(content=nova_response))]
    return mocker.patch("litellm.completion", return_value=mock_resp)
