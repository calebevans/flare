from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flare.config import FlareConfig


class TriggerType(Enum):
    """How the Lambda invocation was triggered."""

    ALARM = "alarm"
    SCHEDULE = "schedule"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True, slots=True)
class TriggerInfo:
    """Parsed metadata about the event that triggered this invocation.

    Carries the trigger type, optional alarm details, and for subscription
    events the raw log lines delivered by CloudWatch Logs.
    """

    trigger_type: TriggerType
    log_group: str | None = None
    alarm_name: str | None = None
    alarm_reason: str | None = None
    raw_logs: str | None = None
    lookback_minutes: int | None = None


def parse_event(event: dict[str, Any], config: FlareConfig) -> TriggerInfo:
    """Route a raw Lambda event to the appropriate trigger parser.

    Detection order:
    1. ``awslogs`` key present -> CloudWatch Logs subscription filter
    2. ``detail-type`` contains "CloudWatch Alarm" -> alarm state change
    3. Anything else -> treated as an EventBridge scheduled invocation
    """
    if "awslogs" in event:
        return _parse_subscription_event(event)
    if "detail-type" in event:
        detail_type = event.get("detail-type", "")
        if "CloudWatch Alarm" in detail_type:
            return _parse_alarm_event(event, config)
    return _parse_schedule_event(config)


def _parse_alarm_event(event: dict[str, Any], config: FlareConfig) -> TriggerInfo:
    """Extract alarm name and state-change reason from an EventBridge alarm event."""
    detail = event.get("detail", {})
    alarm_name = detail.get("alarmName", "Unknown")
    reason = detail.get("state", {}).get("reason", "")
    return TriggerInfo(
        trigger_type=TriggerType.ALARM,
        alarm_name=alarm_name,
        alarm_reason=reason,
        lookback_minutes=config.lookback_minutes,
    )


def _parse_schedule_event(config: FlareConfig) -> TriggerInfo:
    """Create a schedule trigger with the configured lookback window."""
    return TriggerInfo(
        trigger_type=TriggerType.SCHEDULE,
        lookback_minutes=config.lookback_minutes,
    )


def _parse_subscription_event(event: dict[str, Any]) -> TriggerInfo:
    """Decode a CloudWatch Logs subscription payload (base64 + gzip).

    Extracts the log group name and individual log messages, storing
    them as ``raw_logs`` so the handler can skip a separate fetch.
    """
    raw_data = event["awslogs"]["data"]
    decoded = gzip.decompress(base64.b64decode(raw_data))
    payload = json.loads(decoded)

    log_group = payload.get("logGroup", "")
    log_events = payload.get("logEvents", [])
    lines = [ev.get("message", "") for ev in log_events]

    return TriggerInfo(
        trigger_type=TriggerType.SUBSCRIPTION,
        log_group=log_group,
        raw_logs="\n".join(lines),
    )
