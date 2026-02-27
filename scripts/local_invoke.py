"""
Invoke the Flare handler locally against LocalStack.

Requires: LocalStack running (podman-compose up) and seeded (scripts/local_setup.sh).
Requires: .env.local sourced into your shell.

Usage:
    python scripts/local_invoke.py                  # schedule event
    python scripts/local_invoke.py --event alarm
    python scripts/local_invoke.py --event subscription
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _load_event(event_type: str) -> dict:  # type: ignore[type-arg]
    fixture_map = {
        "schedule": "schedule_event.json",
        "alarm": "alarm_event.json",
        "subscription": "subscription_event.json",
    }
    return json.loads((FIXTURES / fixture_map[event_type]).read_text())


def _setup_sqs_subscriber() -> tuple[object, str]:
    """Subscribe an SQS queue to the SNS topic so we can read the message."""
    import boto3

    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    sns = boto3.client("sns", endpoint_url=endpoint, region_name=region)
    sqs = boto3.client("sqs", endpoint_url=endpoint, region_name=region)

    queue = sqs.create_queue(QueueName="flare-local-output")
    queue_url = queue["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    topic_arn = os.environ.get(
        "SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:000000000000:flare-local"
    )
    sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)

    return sqs, queue_url


def _read_sqs_message(sqs: object, queue_url: str) -> str | None:
    resp = sqs.receive_message(  # type: ignore[union-attr]
        QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=2
    )
    messages = resp.get("Messages", [])
    if not messages:
        return None
    body = json.loads(messages[0]["Body"])
    sqs.delete_message(  # type: ignore[union-attr]
        QueueUrl=queue_url, ReceiptHandle=messages[0]["ReceiptHandle"]
    )
    return body.get("Message", body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoke Flare handler locally")
    parser.add_argument(
        "--event",
        choices=["schedule", "alarm", "subscription"],
        default="schedule",
    )
    args = parser.parse_args()

    src = str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    from flare.handler import handler

    sqs, queue_url = _setup_sqs_subscriber()
    event = _load_event(args.event)

    print(f"Invoking handler with {args.event} event...")
    print("=" * 60)

    result = handler(event, None)

    print("=" * 60)
    print(f"Handler returned: {json.dumps(result)}\n")

    message = _read_sqs_message(sqs, queue_url)
    if message:
        print("=" * 60)
        print("SNS MESSAGE (what would be emailed):")
        print("=" * 60)
        print(message)
    else:
        print("(No SNS message published -- likely a healthy scheduled scan)")


if __name__ == "__main__":
    main()
