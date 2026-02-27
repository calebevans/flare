from __future__ import annotations

from typing import Any

from flare.config import FlareConfig
from flare.events import TriggerType, parse_event


def _make_config(**overrides: Any) -> FlareConfig:
    defaults: dict[str, Any] = {
        "log_group_patterns": ["/test/group"],
        "sns_topic_arn": "arn:aws:sns:us-east-1:123:topic",
        "lookback_minutes": 30,
    }
    defaults.update(overrides)
    return FlareConfig(**defaults)


class TestParseAlarmEvent:
    def test_extracts_alarm_fields(self, alarm_event: dict) -> None:
        config = _make_config()
        trigger = parse_event(alarm_event, config)

        assert trigger.trigger_type == TriggerType.ALARM
        assert trigger.alarm_name == "HighCPU-WebServer"
        assert "Threshold Crossed" in (trigger.alarm_reason or "")
        assert trigger.lookback_minutes == 30

    def test_uses_config_lookback(self, alarm_event: dict) -> None:
        config = _make_config(lookback_minutes=60)
        trigger = parse_event(alarm_event, config)

        assert trigger.lookback_minutes == 60


class TestParseScheduleEvent:
    def test_returns_schedule_trigger(self, schedule_event: dict) -> None:
        config = _make_config()
        trigger = parse_event(schedule_event, config)

        assert trigger.trigger_type == TriggerType.SCHEDULE
        assert trigger.lookback_minutes == 30
        assert trigger.alarm_name is None
        assert trigger.raw_logs is None


class TestParseSubscriptionEvent:
    def test_decodes_log_events(self, subscription_event: dict) -> None:
        config = _make_config()
        trigger = parse_event(subscription_event, config)

        assert trigger.trigger_type == TriggerType.SUBSCRIPTION
        assert trigger.log_group == "/aws/lambda/my-app"
        assert trigger.raw_logs is not None
        assert "START RequestId" in trigger.raw_logs
        assert "FATAL Unhandled exception" in trigger.raw_logs

    def test_preserves_all_messages(self, subscription_event: dict) -> None:
        config = _make_config()
        trigger = parse_event(subscription_event, config)

        assert trigger.raw_logs is not None
        lines = trigger.raw_logs.split("\n")
        assert len(lines) == 7
