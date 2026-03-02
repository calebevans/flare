from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest

from flare.config import FlareConfig
from flare.events import TriggerInfo, TriggerType
from flare.prefetch import execute, plan, run


@pytest.fixture()
def trigger() -> TriggerInfo:
    return TriggerInfo(
        trigger_type=TriggerType.ALARM,
        alarm_name="HighErrorRate",
        alarm_reason="ErrorRate > 10%",
    )


def test_plan_parses_json(voice_config: FlareConfig, trigger: TriggerInfo, mocker):
    plan_json = json.dumps(
        {
            "metrics": [
                {
                    "query_key": "RDS connections",
                    "namespace": "AWS/RDS",
                    "metric_name": "DatabaseConnections",
                    "dimensions": {"DBInstanceIdentifier": "auth-db"},
                    "stat": "Average",
                    "period_minutes": 60,
                }
            ],
            "log_queries": [],
            "status_checks": [],
        }
    )

    mock_resp = Mock()
    mock_resp.choices = [Mock(message=Mock(content=plan_json))]
    mocker.patch("litellm.completion", return_value=mock_resp)

    result = plan("STATUS: High\nSUMMARY: test", trigger, voice_config)
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["namespace"] == "AWS/RDS"


def test_plan_handles_markdown_fences(
    voice_config: FlareConfig, trigger: TriggerInfo, mocker
):
    plan_json = '```json\n{"metrics": [], "log_queries": [], "status_checks": []}\n```'

    mock_resp = Mock()
    mock_resp.choices = [Mock(message=Mock(content=plan_json))]
    mocker.patch("litellm.completion", return_value=mock_resp)

    result = plan("STATUS: Low\nSUMMARY: ok", trigger, voice_config)
    assert result == {"metrics": [], "log_queries": [], "status_checks": []}


def test_plan_handles_invalid_json(
    voice_config: FlareConfig, trigger: TriggerInfo, mocker
):
    mock_resp = Mock()
    mock_resp.choices = [Mock(message=Mock(content="Not valid JSON at all"))]
    mocker.patch("litellm.completion", return_value=mock_resp)

    result = plan("STATUS: Low\nSUMMARY: ok", trigger, voice_config)
    assert result == {
        "metrics": [],
        "log_queries": [],
        "status_checks": [],
        "resource_lookups": [],
    }


def test_execute_runs_queries(
    voice_config: FlareConfig, prefetch_plan: dict[str, Any], mocker
):
    mocker.patch(
        "flare.tools.query_metrics",
        return_value={
            "namespace": "AWS/RDS",
            "metric_name": "DatabaseConnections",
            "datapoints": [{"timestamp": "2026-02-26T12:00:00", "value": 95}],
        },
    )
    mocker.patch(
        "flare.tools.query_logs",
        return_value={
            "log_group": "/aws/lambda/auth-service",
            "event_count": 5,
            "sample_lines": ["ERROR test"],
        },
    )

    result = execute(prefetch_plan, voice_config)
    assert len(result["metrics"]) == 1
    assert len(result["logs"]) == 1
    assert result["metrics"][0]["query_key"] == "RDS DatabaseConnections for auth-db"


def test_execute_handles_empty_plan(voice_config: FlareConfig):
    empty_plan = {"metrics": [], "log_queries": [], "status_checks": []}
    result = execute(empty_plan, voice_config)
    assert result == {"metrics": [], "logs": [], "status": [], "resources": []}


def test_run_orchestrates_pipeline(
    voice_config: FlareConfig, trigger: TriggerInfo, mocker
):
    plan_json = json.dumps(
        {
            "metrics": [],
            "log_queries": [],
            "status_checks": [],
        }
    )
    mock_resp = Mock()
    mock_resp.choices = [Mock(message=Mock(content=plan_json))]
    mocker.patch("litellm.completion", return_value=mock_resp)

    mock_put = mocker.patch("flare.store.update_cached_data")

    run("incident-001", "STATUS: High\nSUMMARY: test", trigger, voice_config)

    mock_put.assert_called_once()
    args = mock_put.call_args
    assert args[0][0] == "incident-001"
    assert args[1]["status"] == "complete"
