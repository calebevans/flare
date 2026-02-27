from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("TQDM_DISABLE", "1")  # noqa: E402

from typing import Any  # noqa: E402

from flare.analyzer import analyze_logs
from flare.budget import SourcePlan, compute_available_tokens, plan_token_budget
from flare.config import FlareConfig
from flare.events import TriggerInfo, TriggerType, parse_event
from flare.logs import fetch_logs, resolve_log_groups
from flare.notifier import notify
from flare.triage import build_trigger_context, get_system_prompt, triage

logger = logging.getLogger(__name__)


def _fetch_all_logs(config: FlareConfig, trigger: TriggerInfo) -> dict[str, str]:
    """Fetch log text for every configured log group.

    For subscription triggers, raw logs delivered in the event are used
    directly instead of re-fetching from CloudWatch.
    """
    log_groups = resolve_log_groups(config.log_group_patterns)
    if not log_groups:
        logger.warning("No log groups matched the configured patterns")
        return {}

    log_sources: dict[str, str] = {}
    for log_group in log_groups:
        if trigger.raw_logs and trigger.log_group == log_group:
            log_sources[log_group] = trigger.raw_logs
            continue
        lookback = trigger.lookback_minutes or config.lookback_minutes
        text = fetch_logs(log_group, lookback)
        if text.strip():
            log_sources[log_group] = text
    return log_sources


def _build_section_label(plan: SourcePlan) -> str:
    """Build a human-readable header like ``[/aws/lambda/foo] (reduced to top 45%)``."""
    if plan.needs_reduction and plan.anomaly_percentile is not None:
        return f"[{plan.log_group}] (reduced to top {plan.anomaly_percentile:.0%})"
    return f"[{plan.log_group}] (full logs)"


def _process_sources(plans: list[SourcePlan], config: FlareConfig) -> str:
    """Combine all source plans into a single labeled text block.

    Sources that need reduction are passed through Cordon; others are
    included as raw logs.
    """
    sections: list[str] = []
    for plan in plans:
        label = _build_section_label(plan)
        if plan.needs_reduction and plan.anomaly_percentile is not None:
            reduced = analyze_logs(plan.log_text, plan.anomaly_percentile, config)
            sections.append(f"{label}\n{reduced}")
        else:
            sections.append(f"{label}\n{plan.log_text}")
    return "\n\n".join(sections)


def _configure_logging() -> None:
    """Set up logging and suppress noisy third-party loggers."""
    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    for name in (
        "sentence_transformers",
        "transformers",
        "LiteLLM",
        "litellm",
        "botocore",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point: analyse logs and notify via SNS and voice."""
    _configure_logging()
    config = FlareConfig.from_env()
    trigger = parse_event(event, config)

    log_sources = _fetch_all_logs(config, trigger)
    if not log_sources:
        logger.info("No logs found for any configured log group")
        return {"statusCode": 200, "body": "No logs found"}

    system_prompt = get_system_prompt()
    trigger_context = build_trigger_context(trigger)
    available = compute_available_tokens(config, system_prompt, trigger_context)
    plans = plan_token_budget(log_sources, available, config)

    combined_input = _process_sources(plans, config)

    analysis = triage(combined_input, trigger, config)

    if _is_healthy(analysis) and trigger.trigger_type == TriggerType.SCHEDULE:
        logger.info("Scheduled scan found no issues, skipping notification")
        return {"statusCode": 200, "body": "Healthy, no notification sent"}

    notify(analysis, trigger, config)

    if config.connect_enabled:
        _start_voice_pipeline(analysis, trigger, config)

    logger.info("Analysis complete and published to SNS")
    return {"statusCode": 200, "body": "Analysis complete"}


def _start_voice_pipeline(
    analysis: str, trigger: TriggerInfo, config: FlareConfig
) -> None:
    """Store the incident, pre-fetch investigation data, and call the engineer.

    The outbound call and pre-fetch run in parallel so cached data is
    ready before the engineer answers.  Failures are logged but never
    block the SNS notification that was already sent.
    """
    from flare import caller, prefetch, store

    try:
        incident_id = store.put_incident(analysis, trigger, config)
        with ThreadPoolExecutor(max_workers=2) as pool:
            call_future = pool.submit(caller.start_voice_call, incident_id, config)
            prefetch_future = pool.submit(
                prefetch.run, incident_id, analysis, trigger, config
            )
            call_future.result()
            prefetch_future.result()
    except Exception:
        logger.exception("Voice pipeline failed, SNS notification was still sent")


def _is_healthy(analysis: str) -> bool:
    """Return True if the analysis contains a ``STATUS: Healthy`` line."""
    for line in analysis.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("STATUS:") and "HEALTHY" in stripped:
            return True
    return False
