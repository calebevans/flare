from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

import litellm

from flare import store, tools

if TYPE_CHECKING:
    from flare.config import FlareConfig
    from flare.events import TriggerInfo

logger = logging.getLogger(__name__)

_QUERY_TIMEOUT_SECONDS = 5
_MAX_WORKERS = 8


@cache
def _load_prefetch_prompt() -> str:
    """Load and cache the pre-fetch system prompt from ``prompts/prefetch.txt``."""
    resource = files("flare").joinpath("prompts/prefetch.txt")
    return resource.read_text(encoding="utf-8")


def run(
    incident_id: str,
    analysis: str,
    trigger: TriggerInfo,
    config: FlareConfig,
) -> None:
    """Orchestrate the full pre-fetch pipeline: plan, execute, and cache.

    On failure the incident's ``prefetch_status`` is set to ``"failed"``
    so the fulfillment Lambda knows to fall back to live queries.
    """
    try:
        prefetch_plan = plan(analysis, trigger, config)
        cached_data = execute(prefetch_plan, config)
        store.update_cached_data(incident_id, cached_data, config, status="complete")
    except Exception:
        logger.exception("Pre-fetch failed for incident %s", incident_id)
        try:
            store.update_cached_data(incident_id, {}, config, status="failed")
        except Exception:
            logger.exception("Failed to update prefetch status for %s", incident_id)


def plan(analysis: str, trigger: TriggerInfo, config: FlareConfig) -> dict[str, Any]:
    """Ask Nova 2 Lite which CloudWatch queries an engineer would run next.

    Returns a structured dict of metrics, log queries, and status checks
    parsed from the LLM's JSON response.  Falls back to an empty plan if
    the response cannot be parsed.
    """
    system_prompt = _load_prefetch_prompt()

    user_prompt = (
        f"--- TRIGGER CONTEXT ---\n{trigger.format_context()}\n\n"
        f"--- LOG GROUPS ---\n{chr(10).join(config.log_group_patterns)}\n\n"
        f"--- INCIDENT ANALYSIS ---\n{analysis}"
    )

    response: Any = litellm.completion(
        model=config.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
        temperature=0.2,
    )
    raw = str(response.choices[0].message.content).strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        raw = "\n".join(lines)

    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        logger.warning("Failed to parse pre-fetch plan JSON: %s", raw[:200])
        return {
            "metrics": [],
            "log_queries": [],
            "status_checks": [],
            "resource_lookups": [],
        }


def execute(prefetch_plan: dict[str, Any], config: FlareConfig) -> dict[str, Any]:
    """Run all planned CloudWatch queries in parallel and return results.

    Each query has an individual timeout.  Failed queries are logged
    but do not block the rest.
    """
    cached: dict[str, Any] = {"metrics": [], "logs": [], "status": [], "resources": []}

    tasks: list[dict[str, Any]] = []
    for m in prefetch_plan.get("metrics", []):
        tasks.append({"type": "metric", "spec": m})
    for lq in prefetch_plan.get("log_queries", []):
        tasks.append({"type": "log", "spec": lq})
    for sc in prefetch_plan.get("status_checks", []):
        tasks.append({"type": "status", "spec": sc})
    for rl in prefetch_plan.get("resource_lookups", []):
        tasks.append({"type": "resource", "spec": rl})

    if not tasks:
        return cached

    def _run_task(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        spec = task["spec"]
        if task["type"] == "metric":
            result = tools.query_metrics(
                namespace=spec.get("namespace", ""),
                metric_name=spec.get("metric_name", ""),
                dimensions=spec.get("dimensions", {}),
                period_minutes=spec.get("period_minutes", 60),
                stat=spec.get("stat", "Average"),
            )
            result["query_key"] = spec.get("query_key", "")
            return ("metrics", result)
        elif task["type"] == "log":
            result = tools.query_logs(
                log_group=spec.get("log_group", ""),
                filter_pattern=spec.get("filter_pattern", ""),
                lookback_minutes=spec.get("lookback_minutes", 60),
            )
            result["query_key"] = spec.get("query_key", "")
            return ("logs", result)
        elif task["type"] == "resource":
            result = tools.describe_resource(
                service=spec.get("service", ""),
                operation=spec.get("operation", ""),
            )
            result["query_key"] = spec.get("query_key", "")
            return ("resources", result)
        else:
            result = tools.check_resource_status(
                resource_type=spec.get("resource_type", ""),
                resource_id=spec.get("resource_id", ""),
            )
            result["query_key"] = spec.get("query_key", "")
            return ("status", result)

    with ThreadPoolExecutor(max_workers=min(len(tasks), _MAX_WORKERS)) as pool:
        futures = {pool.submit(_run_task, t): t for t in tasks}
        for future in as_completed(futures, timeout=_QUERY_TIMEOUT_SECONDS * 2):
            try:
                category, result = future.result(timeout=_QUERY_TIMEOUT_SECONDS)
                cached[category].append(result)
            except Exception:
                task_spec = futures[future]
                key = task_spec.get("spec", {}).get("query_key", "unknown")
                logger.warning("Pre-fetch query failed: %s", key)

    return cached
