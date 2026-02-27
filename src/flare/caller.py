from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from flare.config import FlareConfig

logger = logging.getLogger(__name__)

_connect_config: dict[str, str] | None = None


def _load_connect_config() -> dict[str, str]:
    """Read Connect instance/flow/phone config from SSM Parameter Store.

    Cached after first call to avoid repeated SSM lookups.
    """
    global _connect_config  # noqa: PLW0603
    if _connect_config is not None:
        return _connect_config

    param_name = os.environ.get("CONNECT_CONFIG_PARAM", "")
    if not param_name:
        raise ValueError("CONNECT_CONFIG_PARAM not set")

    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=param_name)
    _connect_config = json.loads(resp["Parameter"]["Value"])
    return _connect_config


def start_voice_call(
    incident_id: str,
    config: FlareConfig,
) -> str | None:
    """Place an outbound call via Amazon Connect.

    Reads Connect instance ID, contact flow ARN, and phone number from
    an SSM parameter (populated by CloudFormation).  Passes *incident_id*
    as a contact attribute so the contact flow can retrieve the RCA from
    DynamoDB.  Returns the Connect contact ID on success, or ``None`` if
    the call fails (logged, never raised).
    """
    try:
        cc = _load_connect_config()
        connect_client = boto3.client("connect")
        response = connect_client.start_outbound_voice_contact(
            DestinationPhoneNumber=config.oncall_phone,
            ContactFlowId=cc["contact_flow_arn"],
            InstanceId=cc["instance_id"],
            SourcePhoneNumber=cc["phone_number"],
            Attributes={"incident_id": incident_id},
        )
        contact_id: str = response["ContactId"]
        logger.info(
            "Outbound call initiated: contact_id=%s, incident_id=%s",
            contact_id,
            incident_id,
        )
        return contact_id
    except Exception:
        logger.exception("Failed to start outbound voice call for %s", incident_id)
        return None
