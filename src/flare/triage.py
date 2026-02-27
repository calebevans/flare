from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING, Any

import litellm

from flare.events import TriggerInfo, TriggerType

if TYPE_CHECKING:
    from flare.config import FlareConfig

_SYSTEM_PROMPT: str | None = None


def _load_system_prompt() -> str:
    """Load and cache the triage system prompt from ``prompts/triage.txt``."""
    global _SYSTEM_PROMPT  # noqa: PLW0603
    if _SYSTEM_PROMPT is None:
        resource = files("flare").joinpath("prompts/triage.txt")
        _SYSTEM_PROMPT = resource.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


def _build_trigger_context(trigger: TriggerInfo) -> str:
    """Format trigger metadata into a human-readable context string.

    Includes the trigger type, alarm name/reason if present, and a
    note for scheduled scans so the LLM calibrates its assessment.
    """
    parts: list[str] = [f"Trigger type: {trigger.trigger_type.value}"]
    if trigger.alarm_name:
        parts.append(f"Alarm: {trigger.alarm_name}")
    if trigger.alarm_reason:
        parts.append(f"Reason: {trigger.alarm_reason}")
    if trigger.trigger_type == TriggerType.SCHEDULE:
        parts.append("This is a scheduled scan, not triggered by a specific alarm.")
    return "\n".join(parts)


def triage(
    log_content: str,
    trigger: TriggerInfo,
    config: FlareConfig,
) -> str:
    """Send log content to Nova 2 Lite and return a structured RCA.

    Combines the triage system prompt with trigger context and log data,
    then calls litellm at temperature 0.3.  The response follows the
    STATUS / SUMMARY / EVIDENCE / NEXT STEPS format defined in
    ``prompts/triage.txt``.
    """
    system_prompt = _load_system_prompt()
    trigger_context = _build_trigger_context(trigger)

    user_prompt = (
        f"--- TRIGGER CONTEXT ---\n{trigger_context}\n\n--- LOG DATA ---\n{log_content}"
    )

    response: Any = litellm.completion(
        model=config.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=config.max_output_tokens,
        temperature=0.3,
    )
    return str(response.choices[0].message.content)


def get_system_prompt() -> str:
    """Public accessor for the cached triage system prompt."""
    return _load_system_prompt()


def build_trigger_context(trigger: TriggerInfo) -> str:
    """Public accessor for building trigger context from a TriggerInfo."""
    return _build_trigger_context(trigger)
