from __future__ import annotations

import pytest

from flare.config import FlareConfig


class TestFlareConfigFromEnv:
    def test_parses_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_GROUP_PATTERNS", "/app/web,/app/api")
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")

        config = FlareConfig.from_env()

        assert config.log_group_patterns == ["/app/web", "/app/api"]
        assert config.sns_topic_arn == "arn:aws:sns:us-east-1:123:topic"

    def test_applies_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_GROUP_PATTERNS", "/app/web")
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")

        config = FlareConfig.from_env()

        assert config.lookback_minutes == 30
        assert config.cordon_window_size == 4
        assert config.token_budget == 0
        assert config.max_output_tokens == 4096

    def test_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_GROUP_PATTERNS", "/app/web")
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
        monkeypatch.setenv("LOOKBACK_MINUTES", "60")
        monkeypatch.setenv("TOKEN_BUDGET", "50000")

        config = FlareConfig.from_env()

        assert config.lookback_minutes == 60
        assert config.token_budget == 50_000

    def test_raises_when_log_groups_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
        monkeypatch.delenv("LOG_GROUP_PATTERNS", raising=False)

        with pytest.raises(ValueError, match="LOG_GROUP_PATTERNS"):
            FlareConfig.from_env()

    def test_raises_when_sns_arn_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_GROUP_PATTERNS", "/app/web")
        monkeypatch.delenv("SNS_TOPIC_ARN", raising=False)

        with pytest.raises(ValueError, match="SNS_TOPIC_ARN"):
            FlareConfig.from_env()

    def test_strips_whitespace_from_log_groups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_GROUP_PATTERNS", " /app/web , /app/api ")
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")

        config = FlareConfig.from_env()

        assert config.log_group_patterns == ["/app/web", "/app/api"]


class TestNovaLitellmModel:
    def test_adds_bedrock_prefix(self) -> None:
        config = FlareConfig(
            log_group_patterns=["/g"],
            sns_topic_arn="arn:x",
            nova_model_id="us.amazon.nova-2-lite-v1:0",
        )
        assert config.litellm_model == "bedrock/us.amazon.nova-2-lite-v1:0"

    def test_preserves_existing_prefix(self) -> None:
        config = FlareConfig(
            log_group_patterns=["/g"],
            sns_topic_arn="arn:x",
            nova_model_id="bedrock/us.amazon.nova-2-lite-v1:0",
        )
        assert config.litellm_model == "bedrock/us.amazon.nova-2-lite-v1:0"
