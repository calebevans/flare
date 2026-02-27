from __future__ import annotations

from unittest.mock import Mock, patch

from flare.caller import start_voice_call
from flare.config import FlareConfig


def test_start_voice_call_success(voice_config: FlareConfig, monkeypatch):
    monkeypatch.setenv("CONNECT_CONFIG_PARAM", "/flare/test/connect-config")

    ssm_response = {
        "Parameter": {
            "Value": '{"instance_id":"test-instance-id",'
            '"contact_flow_arn":"test-flow-id",'
            '"phone_number":"+15551234567"}'
        }
    }

    mock_ssm = Mock()
    mock_ssm.get_parameter.return_value = ssm_response

    mock_connect = Mock()
    mock_connect.start_outbound_voice_contact.return_value = {
        "ContactId": "contact-abc-123"
    }

    import flare.caller

    flare.caller._connect_config = None

    with patch("boto3.client") as mock_boto:
        mock_boto.side_effect = lambda svc: mock_ssm if svc == "ssm" else mock_connect
        result = start_voice_call("incident-001", voice_config)

    assert result == "contact-abc-123"
    mock_connect.start_outbound_voice_contact.assert_called_once_with(
        DestinationPhoneNumber="+15559876543",
        ContactFlowId="test-flow-id",
        InstanceId="test-instance-id",
        SourcePhoneNumber="+15551234567",
        Attributes={"incident_id": "incident-001"},
    )


def test_start_voice_call_failure(voice_config: FlareConfig, monkeypatch):
    monkeypatch.setenv("CONNECT_CONFIG_PARAM", "/flare/test/connect-config")

    import flare.caller

    flare.caller._connect_config = None

    with patch("boto3.client") as mock_boto:
        mock_boto.side_effect = Exception("SSM error")
        result = start_voice_call("incident-001", voice_config)

    assert result is None
