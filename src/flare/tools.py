from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any, cast

import boto3

logger = logging.getLogger(__name__)


def query_metrics(
    namespace: str,
    metric_name: str,
    dimensions: dict[str, str],
    period_minutes: int = 60,
    stat: str = "Average",
    *,
    cloudwatch_client: Any | None = None,
) -> dict[str, Any]:
    """Fetch CloudWatch metric statistics for a given resource.

    Returns a dict with sorted datapoints on success, or an ``error``
    key on failure.  The period is automatically divided into ~10
    data points (minimum 60 seconds).
    """
    if cloudwatch_client is None:
        cloudwatch_client = boto3.client("cloudwatch")

    end_time = datetime.now(tz=UTC)
    start_time = end_time - __import__("datetime").timedelta(minutes=period_minutes)

    cw_dimensions: list[dict[str, str]] = [
        {"Name": k, "Value": v} for k, v in dimensions.items()
    ]

    try:
        response = cloudwatch_client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=cast("Any", cw_dimensions),
            StartTime=start_time,
            EndTime=end_time,
            Period=max(period_minutes * 60 // 10, 60),
            Statistics=cast("Any", [stat]),
        )
        datapoints = sorted(
            response.get("Datapoints", []),
            key=lambda dp: dp.get("Timestamp", ""),
        )
        serialized = []
        for dp in datapoints:
            ts = dp["Timestamp"]
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            serialized.append(
                {
                    "timestamp": ts_str,
                    "value": dp.get(stat, 0),
                    "unit": dp.get("Unit", ""),
                }
            )

        return {
            "namespace": namespace,
            "metric_name": metric_name,
            "dimensions": dimensions,
            "stat": stat,
            "period_minutes": period_minutes,
            "datapoints": serialized,
        }
    except Exception:
        logger.exception("Failed to query metric %s/%s", namespace, metric_name)
        return {
            "namespace": namespace,
            "metric_name": metric_name,
            "dimensions": dimensions,
            "error": "Failed to retrieve metric data",
        }


def query_logs(
    log_group: str,
    filter_pattern: str = "",
    lookback_minutes: int = 60,
    limit: int = 50,
    *,
    logs_client: Any | None = None,
) -> dict[str, Any]:
    """Search CloudWatch Logs for events matching a filter pattern.

    Returns a dict with event count and sample lines on success,
    or an ``error`` key on failure.
    """
    if logs_client is None:
        logs_client = boto3.client("logs")

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (lookback_minutes * 60 * 1000)

    try:
        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
            "interleaved": True,
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern

        response = logs_client.filter_log_events(**kwargs)
        events = response.get("events", [])

        sample_lines = []
        for event in events:
            ts = event.get("timestamp", 0)
            msg = event.get("message", "").rstrip("\n")
            dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
            sample_lines.append(f"{dt.isoformat()} {msg}")

        return {
            "log_group": log_group,
            "filter_pattern": filter_pattern,
            "lookback_minutes": lookback_minutes,
            "event_count": len(events),
            "sample_lines": sample_lines,
        }
    except Exception:
        logger.exception("Failed to query logs for %s", log_group)
        return {
            "log_group": log_group,
            "filter_pattern": filter_pattern,
            "error": "Failed to retrieve log data",
        }


def check_resource_status(
    resource_type: str,
    resource_id: str,
) -> dict[str, Any]:
    """Check the health of an AWS resource (Lambda, RDS, ECS, or EC2).

    Dispatches to the appropriate describe API based on *resource_type*.
    Returns a dict with ``health`` and ``details`` on success, or an
    ``error`` key for unsupported types or API failures.
    """
    resource_type = resource_type.lower()
    try:
        if resource_type == "lambda":
            return _check_lambda_status(resource_id)
        elif resource_type == "rds":
            return _check_rds_status(resource_id)
        elif resource_type == "ecs":
            return _check_ecs_status(resource_id)
        elif resource_type == "ec2":
            return _check_ec2_status(resource_id)
        else:
            return {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "error": f"Unsupported resource type: {resource_type}",
            }
    except Exception:
        logger.exception("Failed to check status for %s/%s", resource_type, resource_id)
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "error": "Failed to check resource status",
        }


def _check_lambda_status(function_name: str) -> dict[str, Any]:
    """Get Lambda function state, runtime, memory, and last modified time."""
    client = boto3.client("lambda")
    resp = client.get_function(FunctionName=function_name)
    config = resp.get("Configuration", {})
    return {
        "resource_type": "lambda",
        "resource_id": function_name,
        "health": config.get("State", "Unknown"),
        "details": {
            "runtime": config.get("Runtime", ""),
            "memory_mb": config.get("MemorySize", 0),
            "timeout_s": config.get("Timeout", 0),
            "last_modified": config.get("LastModified", ""),
        },
    }


def _check_rds_status(db_identifier: str) -> dict[str, Any]:
    """Get RDS instance status, engine, class, and storage info."""
    client = boto3.client("rds")
    resp = client.describe_db_instances(DBInstanceIdentifier=db_identifier)
    instances = resp.get("DBInstances", [])
    if not instances:
        return {
            "resource_type": "rds",
            "resource_id": db_identifier,
            "health": "NotFound",
        }
    db = instances[0]
    return {
        "resource_type": "rds",
        "resource_id": db_identifier,
        "health": db.get("DBInstanceStatus", "Unknown"),
        "details": {
            "engine": db.get("Engine", ""),
            "instance_class": db.get("DBInstanceClass", ""),
            "multi_az": db.get("MultiAZ", False),
            "storage_gb": db.get("AllocatedStorage", 0),
        },
    }


def _check_ecs_status(service_arn: str) -> dict[str, Any]:
    """Get ECS service status and running/desired/pending task counts."""
    client = boto3.client("ecs")
    parts = service_arn.split("/")
    cluster = parts[1] if len(parts) >= 3 else "default"
    service = parts[-1]
    resp = client.describe_services(cluster=cluster, services=[service])
    services = resp.get("services", [])
    if not services:
        return {
            "resource_type": "ecs",
            "resource_id": service_arn,
            "health": "NotFound",
        }
    svc = services[0]
    return {
        "resource_type": "ecs",
        "resource_id": service_arn,
        "health": svc.get("status", "Unknown"),
        "details": {
            "running_count": svc.get("runningCount", 0),
            "desired_count": svc.get("desiredCount", 0),
            "pending_count": svc.get("pendingCount", 0),
        },
    }


def _check_ec2_status(instance_id: str) -> dict[str, Any]:
    """Get EC2 instance state and system/instance status checks."""
    client = boto3.client("ec2")
    resp = client.describe_instance_status(InstanceIds=[instance_id])
    statuses = resp.get("InstanceStatuses", [])
    if not statuses:
        return {
            "resource_type": "ec2",
            "resource_id": instance_id,
            "health": "NotFound or Stopped",
        }
    status = statuses[0]
    return {
        "resource_type": "ec2",
        "resource_id": instance_id,
        "health": status.get("InstanceState", {}).get("Name", "Unknown"),
        "details": {
            "system_status": status.get("SystemStatus", {}).get("Status", ""),
            "instance_status": status.get("InstanceStatus", {}).get("Status", ""),
        },
    }
