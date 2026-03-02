from __future__ import annotations

import json
import logging
from importlib.resources import files
from typing import Any

import litellm

from flare import store, tools
from flare.config import FlareConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_REASONING_PROMPT: str | None = None


def _load_reasoning_prompt() -> str:
    """Load and cache the reasoning system prompt from ``prompts/reasoning.txt``."""
    global _REASONING_PROMPT  # noqa: PLW0603
    if _REASONING_PROMPT is None:
        resource = files("flare").joinpath("prompts/reasoning.txt")
        _REASONING_PROMPT = resource.read_text(encoding="utf-8")
    return _REASONING_PROMPT


def _get_config() -> FlareConfig:
    """Build config from environment (used by both handler entry points)."""
    return FlareConfig.from_env()


# ---------------------------------------------------------------------------
# Briefing handler -- called by Connect contact flow via Invoke Lambda
# ---------------------------------------------------------------------------


def voice_dispatch(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Route incoming events to the appropriate handler.

    Connect contact flow events contain ``Details.ContactData``.
    Lex fulfillment events contain ``sessionState``.
    """
    if "Details" in event and "ContactData" in event.get("Details", {}):
        return briefing_handler(event, context)
    if "sessionState" in event:
        return fulfillment_handler(event, context)
    return {"statusCode": 400, "body": "Unknown event type"}


def briefing_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Connect contact flow handler: read the RCA from DynamoDB for Polly.

    Extracts ``incident_id`` from the contact attributes, retrieves the
    incident record, and returns ``rca_summary``, ``severity``, and
    ``affected`` for the contact flow's Play Prompt block.
    """
    config = _get_config()

    incident_id = ""
    details = event.get("Details", {})
    contact_data = details.get("ContactData", {})
    attributes = contact_data.get("Attributes", {})
    incident_id = attributes.get("incident_id", "")

    if not incident_id:
        logger.warning("No incident_id in contact attributes")
        return {
            "rca_summary": (
                "An incident has been detected but I'm unable to"
                " retrieve the details. Please check your email."
            ),
            "severity": "Unknown",
            "affected": "Unknown",
        }

    try:
        incident = store.get_incident(incident_id, config)
        rca = incident.get("rca", "")
        alarm_name = incident.get("alarm_name", "infrastructure")
        return {
            "rca_summary": _extract_spoken_summary(rca),
            "severity": _extract_severity(rca),
            "affected": alarm_name,
        }
    except Exception:
        logger.exception("Failed to retrieve incident %s", incident_id)
        return {
            "rca_summary": (
                "An incident has been detected but I encountered"
                " an error retrieving the analysis. Check your email."
            ),
            "severity": "Unknown",
            "affected": "Unknown",
        }


def _extract_severity(rca: str) -> str:
    """Parse the STATUS line from the RCA and return the severity level."""
    for line in rca.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("STATUS:"):
            status = stripped.replace("STATUS:", "").strip()
            if status in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY"):
                return status.capitalize()
    return "Unknown"


def _extract_spoken_summary(rca: str) -> str:
    """Extract the SPOKEN SUMMARY field from the RCA for voice delivery.

    Falls back to the SUMMARY field, then to a generic message if
    neither is found.
    """
    for line in rca.splitlines():
        if line.strip().startswith("SPOKEN SUMMARY:"):
            return line.split(":", 1)[1].strip()
    for line in rca.splitlines():
        if line.strip().startswith("SUMMARY:"):
            return line.split(":", 1)[1].strip()
    return "An incident has been detected. Check your email for details."


# ---------------------------------------------------------------------------
# Fulfillment handler -- called by Lex for intent fulfillment
# Implements the retrieve-then-reason pattern
# ---------------------------------------------------------------------------


def fulfillment_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lex fulfillment handler implementing the retrieve-then-reason pattern.

    Retrieves relevant data from the DynamoDB cache (or live CloudWatch
    on cache miss), then passes the engineer's question, the data, and
    the RCA context to Nova 2 Lite for a conversational answer.
    """
    config = _get_config()

    session_state = event.get("sessionState", {})
    intent = session_state.get("intent", {})
    intent_name = intent.get("name", "")
    slots = intent.get("slots", {})
    session_attrs = session_state.get("sessionAttributes", {})
    incident_id = session_attrs.get("incident_id", "")
    user_question = event.get("inputTranscript", "")

    rca_summary = session_attrs.get("rca_summary", "")
    briefing_delivered = session_attrs.get("briefing_delivered", "")

    incident: dict[str, Any] = {}
    if incident_id:
        try:
            incident = store.get_incident(incident_id, config)
        except Exception:
            logger.exception("Failed to load incident %s", incident_id)

    rca = incident.get("rca", "")

    if intent_name == "Goodbye":
        message = (
            "Glad I could help. The full analysis has been sent to your "
            "email. Good luck out there."
        )
        return {
            "sessionState": {
                "dialogAction": {"type": "Close"},
                "intent": {"name": intent_name, "state": "Fulfilled"},
                "sessionAttributes": session_attrs,
            },
            "messages": [{"contentType": "PlainText", "content": message}],
        }

    if not briefing_delivered and rca_summary:
        message = (
            f"{rca_summary} "
            "I am here to help you investigate. What would you like to know?"
        )
        session_attrs["briefing_delivered"] = "true"
    else:
        try:
            relevant_data = _gather_data_for_question(
                intent_name, slots, incident, config, user_question
            )
            message = _reason_about_data(user_question, relevant_data, rca, config)
        except Exception:
            logger.exception("Reasoning failed for intent %s", intent_name)
            message = (
                "I ran into an issue analyzing the data."
                " Could you try asking that again?"
            )

    return {
        "sessionState": {
            "dialogAction": {"type": "ElicitIntent"},
            "intent": {"name": intent_name, "state": "Fulfilled"},
            "sessionAttributes": session_attrs,
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }


def _gather_data_for_question(
    intent_name: str,
    slots: dict[str, Any],
    incident: dict[str, Any],
    config: FlareConfig,
    user_question: str = "",
) -> Any:
    """Look up data for the engineer's question, preferring the cache.

    For specific intents (CheckMetrics, CheckLogs, CheckStatus), tries
    a fuzzy cache match first and falls back to a live query.  For
    FallbackIntent and Summarize, returns all cached data so the LLM
    can decide what's relevant.
    """
    cached = incident.get("cached_data", {})
    if isinstance(cached, str):
        try:
            cached = json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            cached = {}

    if intent_name == "CheckMetrics":
        hit = _find_cached(cached.get("metrics", []), slots)
        if hit:
            return hit
        return _live_metric_query(slots, config)

    if intent_name == "CheckLogs":
        hit = _find_cached(cached.get("logs", []), slots)
        if hit:
            return hit
        return _live_log_query(slots, config)

    if intent_name == "CheckStatus":
        hit = _find_cached(cached.get("status", []), slots)
        if hit:
            return hit
        live = _live_status_check(slots)
        if "error" not in live:
            return live
        return _smart_resource_lookup(user_question, cached, config)

    if intent_name == "FallbackIntent":
        return _smart_resource_lookup(user_question, cached, config)

    # Summarize -- send all cached data
    return cached


def _find_cached(
    items: list[dict[str, Any]], slots: dict[str, Any]
) -> dict[str, Any] | None:
    """Find a cached item whose ``query_key`` fuzzy-matches the slot values.

    Returns the first item if no slots are provided, or ``None`` if the
    cache is empty.
    """
    if not items:
        return None

    slot_text = " ".join(
        str(s.get("value", {}).get("interpretedValue", ""))
        for s in (slots or {}).values()
        if s and s.get("value")
    ).lower()

    if not slot_text:
        return items[0] if items else None

    for item in items:
        key = str(item.get("query_key", "")).lower()
        if any(word in key for word in slot_text.split() if len(word) > 2):
            return item

    return items[0] if items else None


def _live_metric_query(slots: dict[str, Any], config: FlareConfig) -> dict[str, Any]:
    """Fall back to a live CloudWatch metric query when the cache misses."""
    metric_slot = _slot_value(slots, "metric")
    resource_slot = _slot_value(slots, "resource")
    return tools.query_metrics(
        namespace=_guess_namespace(resource_slot),
        metric_name=metric_slot or "CPUUtilization",
        dimensions=_guess_dimensions(resource_slot),
        period_minutes=60,
    )


def _live_log_query(slots: dict[str, Any], config: FlareConfig) -> dict[str, Any]:
    """Fall back to a live CloudWatch Logs query when the cache misses."""
    service_slot = _slot_value(slots, "service") or _slot_value(slots, "log_group")
    patterns = config.log_group_patterns
    log_group = service_slot or (patterns[0] if patterns else "")
    return tools.query_logs(
        log_group=log_group,
        filter_pattern="ERROR",
        lookback_minutes=60,
    )


def _live_status_check(slots: dict[str, Any]) -> dict[str, Any]:
    """Fall back to a live resource status check when the cache misses."""
    resource_slot = _slot_value(slots, "resource")
    resource_type = _slot_value(slots, "resource_type") or "lambda"
    return tools.check_resource_status(
        resource_type=resource_type,
        resource_id=resource_slot or "unknown",
    )


def _slot_value(slots: dict[str, Any], name: str) -> str:
    """Extract the interpreted value from a Lex slot, or empty string."""
    slot = slots.get(name)
    if slot and isinstance(slot, dict):
        value = slot.get("value", {})
        if isinstance(value, dict):
            return str(value.get("interpretedValue", ""))
    return ""


def _guess_namespace(resource_hint: str) -> str:
    """Infer a CloudWatch namespace from a resource name hint."""
    hint = resource_hint.lower()
    if "rds" in hint or "database" in hint or "db" in hint:
        return "AWS/RDS"
    if "lambda" in hint or "function" in hint:
        return "AWS/Lambda"
    if "ec2" in hint or "instance" in hint:
        return "AWS/EC2"
    if "ecs" in hint or "container" in hint or "service" in hint:
        return "AWS/ECS"
    if "api" in hint or "gateway" in hint:
        return "AWS/ApiGateway"
    if "elb" in hint or "load" in hint or "balancer" in hint:
        return "AWS/ELB"
    return "AWS/EC2"


def _guess_dimensions(resource_hint: str) -> dict[str, str]:
    """Infer CloudWatch dimension key/value from a resource name hint."""
    if not resource_hint:
        return {}
    hint = resource_hint.lower()
    if "rds" in hint or "database" in hint or "db" in hint:
        return {"DBInstanceIdentifier": resource_hint}
    if "lambda" in hint or "function" in hint:
        return {"FunctionName": resource_hint}
    return {"InstanceId": resource_hint}


def _smart_resource_lookup(
    question: str,
    cached: dict[str, Any],
    config: FlareConfig,
) -> Any:
    """Ask Nova 2 Lite which AWS API to call, then execute it.

    Returns the live API result merged with any cached data so the
    reasoning step has everything it needs.  On failure, returns a
    structured error so the reasoning LLM can communicate the issue
    honestly instead of silently omitting data.
    """
    cached_summary = json.dumps(cached, default=str)
    if len(cached_summary) > 4000:
        cached_summary = cached_summary[:4000] + "..."

    plan_prompt = (
        f'The engineer asked: "{question}"\n\n'
        f"Cached investigation data:\n{cached_summary}\n\n"
        "If the cached data can answer this question, respond with "
        'ONLY the JSON: {"use_cache": true}\n\n'
        "Otherwise, suggest ONE AWS API call to answer it. Respond "
        "with ONLY valid JSON:\n"
        '{"service": "<aws_service>", "operation": "<snake_case_method>", '
        '"params": {<optional_filters>}}\n\n'
        "Only use read-only operations (describe_*, get_*, list_*). "
        "Use snake_case for the operation name. "
        "You have full read-only access to all AWS services. "
        "Choose the API call that best answers the engineer's question."
    )

    try:
        resp: Any = litellm.completion(
            model=config.litellm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You suggest AWS API calls to investigate "
                        "infrastructure incidents. Respond with JSON only."
                    ),
                },
                {"role": "user", "content": plan_prompt},
            ],
            max_tokens=200,
            temperature=0.0,
        )
        raw = str(resp.choices[0].message.content).strip()
    except Exception:
        logger.exception("Smart lookup LLM call failed")
        return {
            "cached": cached,
            "lookup_error": "Failed to determine which AWS API to call.",
        }

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        logger.warning("Smart lookup returned non-JSON response: %s", raw[:200])
        return {
            "cached": cached,
            "lookup_error": "Could not parse an API call plan from the model response.",
        }

    try:
        plan = json.loads(raw[start:end])
    except json.JSONDecodeError:
        logger.warning("Smart lookup JSON parse failed: %s", raw[:200])
        return {
            "cached": cached,
            "lookup_error": "Model returned malformed JSON for the API call plan.",
        }

    if plan.get("use_cache"):
        return cached

    service = plan.get("service", "")
    operation = plan.get("operation", "")
    params = plan.get("params")
    logger.info("Smart lookup plan: %s.%s(%s)", service, operation, params)

    result = tools.describe_resource(
        service=service,
        operation=operation,
        params=params,
    )

    if "error" in result:
        logger.warning("Smart lookup API call failed: %s", result["error"])
        return {
            "cached": cached,
            "lookup_error": (
                f"Attempted {service}.{operation} but it failed: {result['error']}"
            ),
        }

    logger.info(
        "Smart lookup result keys: %s",
        list(result.keys()) if isinstance(result, dict) else type(result),
    )
    return {"cached": cached, "live_lookup": result}


def _reason_about_data(
    question: str,
    data: Any,
    rca: str,
    config: FlareConfig,
) -> str:
    """Ask Nova 2 Lite to answer the engineer's question using retrieved data.

    Combines the question, retrieved investigation data, and RCA context
    into a single prompt.  The response is capped at 300 tokens (~800
    chars) for voice-friendly output.
    """
    system_prompt = _load_reasoning_prompt()

    data_str = json.dumps(data, default=str) if data else "No data available."
    if len(data_str) > 8000:
        data_str = data_str[:8000] + "... (truncated)"

    user_prompt = (
        f'The engineer asked: "{question}"\n\n'
        f"Incident analysis:\n{rca}\n\n"
        f"Retrieved data:\n{data_str}\n\n"
        "Answer the engineer's question conversationally in 2-4 sentences. "
        "Correlate the data with the incident analysis when relevant. "
        "Lead with the key finding. Be direct and natural."
    )

    response: Any = litellm.completion(
        model=config.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=300,
        temperature=0.3,
    )
    return str(response.choices[0].message.content)
