from __future__ import annotations

from typing import TYPE_CHECKING

from flare.config import FlareConfig
from flare.events import TriggerInfo, TriggerType
from flare.triage import build_trigger_context, triage

if TYPE_CHECKING:
    from unittest.mock import Mock

_CONFIG = FlareConfig(
    log_group_patterns=[],
    sns_topic_arn="arn:x",
    nova_model_id="us.amazon.nova-2-lite-v1:0",
    max_output_tokens=4096,
)


class TestBuildTriggerContext:
    def test_alarm_trigger(self) -> None:
        trigger = TriggerInfo(
            trigger_type=TriggerType.ALARM,
            alarm_name="HighCPU",
            alarm_reason="Threshold exceeded",
        )
        ctx = build_trigger_context(trigger)

        assert "alarm" in ctx
        assert "HighCPU" in ctx
        assert "Threshold exceeded" in ctx

    def test_schedule_trigger(self) -> None:
        trigger = TriggerInfo(trigger_type=TriggerType.SCHEDULE)
        ctx = build_trigger_context(trigger)

        assert "scheduled scan" in ctx.lower()

    def test_subscription_trigger(self) -> None:
        trigger = TriggerInfo(
            trigger_type=TriggerType.SUBSCRIPTION,
            log_group="/app/web",
        )
        ctx = build_trigger_context(trigger)

        assert "subscription" in ctx


class TestTriage:
    def test_returns_nova_response(
        self,
        mock_litellm_completion: Mock,
        nova_response: str,
    ) -> None:
        trigger = TriggerInfo(trigger_type=TriggerType.SCHEDULE)

        result = triage("some log content", trigger, _CONFIG)

        assert result == nova_response

    def test_passes_log_content_in_prompt(
        self,
        mock_litellm_completion: Mock,
    ) -> None:
        trigger = TriggerInfo(trigger_type=TriggerType.SCHEDULE)

        triage("UNIQUE_LOG_CONTENT_XYZ", trigger, _CONFIG)

        call_args = mock_litellm_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[1]["content"]
        assert "UNIQUE_LOG_CONTENT_XYZ" in user_msg

    def test_includes_system_prompt(
        self,
        mock_litellm_completion: Mock,
    ) -> None:
        trigger = TriggerInfo(trigger_type=TriggerType.SCHEDULE)

        triage("logs", trigger, _CONFIG)

        call_args = mock_litellm_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_msg = messages[0]["content"]
        assert "Flare" in system_msg
