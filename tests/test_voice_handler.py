from __future__ import annotations

from unittest.mock import Mock

import pytest

from flare.voice_handler import briefing_handler, fulfillment_handler


@pytest.fixture(autouse=True)
def _env_vars(monkeypatch, voice_config):
    monkeypatch.setenv("LOG_GROUP_PATTERNS", "/test/log-group")
    monkeypatch.setenv("SNS_TOPIC_ARN", voice_config.sns_topic_arn)
    monkeypatch.setenv("INCIDENTS_TABLE_NAME", voice_config.incidents_table_name)
    monkeypatch.setenv("CONNECT_ENABLED", "true")
    monkeypatch.setenv("CONNECT_INSTANCE_ID", voice_config.connect_instance_id)
    monkeypatch.setenv("CONNECT_CONTACT_FLOW_ID", voice_config.connect_contact_flow_id)
    monkeypatch.setenv("CONNECT_PHONE_NUMBER", voice_config.connect_phone_number)
    monkeypatch.setenv("ONCALL_PHONE", voice_config.oncall_phone)


class TestBriefingHandler:
    def test_returns_rca_summary(
        self, connect_briefing_event: dict, sample_incident: dict, mocker
    ):
        mocker.patch("flare.store.get_incident", return_value=sample_incident)

        result = briefing_handler(connect_briefing_event, None)

        assert "Connection pool exhaustion" in result["rca_summary"]
        assert result["severity"] == "High"
        assert result["affected"] == "HighErrorRate-APIGateway"

    def test_handles_missing_incident_id(self, mocker):
        event = {"Details": {"ContactData": {"Attributes": {}}}}

        result = briefing_handler(event, None)

        assert "unable to retrieve" in result["rca_summary"].lower()

    def test_handles_store_error(self, connect_briefing_event: dict, mocker):
        mocker.patch(
            "flare.store.get_incident",
            side_effect=Exception("DynamoDB error"),
        )

        result = briefing_handler(connect_briefing_event, None)

        assert "error" in result["rca_summary"].lower()


class TestFulfillmentHandler:
    def test_cache_hit_with_reasoning(
        self,
        lex_fulfillment_event: dict,
        sample_incident: dict,
        mocker,
    ):
        mocker.patch("flare.store.get_incident", return_value=sample_incident)

        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(
            content="It does look overwhelmed. Connections peaked at 98 out of 100 max."
        ))]
        mocker.patch("litellm.completion", return_value=mock_resp)

        result = fulfillment_handler(lex_fulfillment_event, None)

        assert result["sessionState"]["intent"]["state"] == "Fulfilled"
        msg = result["messages"][0]["content"]
        assert "overwhelmed" in msg.lower() or "connections" in msg.lower()

    def test_fallback_intent_sends_all_data(
        self,
        sample_incident: dict,
        mocker,
    ):
        mocker.patch("flare.store.get_incident", return_value=sample_incident)

        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(
            content="Based on the data, this looks like a connection pool issue."
        ))]
        mock_completion = mocker.patch("litellm.completion", return_value=mock_resp)

        event = {
            "sessionState": {
                "intent": {
                    "name": "FallbackIntent",
                    "state": "InProgress",
                    "slots": {},
                },
                "sessionAttributes": {"incident_id": "test-incident-123"},
            },
            "inputTranscript": "Could this be related to a deployment?",
        }

        result = fulfillment_handler(event, None)

        assert result["sessionState"]["intent"]["state"] == "Fulfilled"
        call_args = mock_completion.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        assert "deployment" in user_msg

    def test_handles_reasoning_error(
        self,
        lex_fulfillment_event: dict,
        sample_incident: dict,
        mocker,
    ):
        mocker.patch("flare.store.get_incident", return_value=sample_incident)
        mocker.patch("litellm.completion", side_effect=Exception("Bedrock error"))

        result = fulfillment_handler(lex_fulfillment_event, None)

        msg = result["messages"][0]["content"]
        assert "issue" in msg.lower() or "try" in msg.lower()

    def test_handles_missing_incident(
        self,
        lex_fulfillment_event: dict,
        mocker,
    ):
        mocker.patch("flare.store.get_incident", return_value={})

        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(
            content="I don't have data for that right now."
        ))]
        mocker.patch("litellm.completion", return_value=mock_resp)

        result = fulfillment_handler(lex_fulfillment_event, None)
        assert result["messages"][0]["content"]
