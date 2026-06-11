# Flare

🏆 Best of Voice AI category winner in the [Amazon Nova AI Hackathon](https://amazon-nova.devpost.com) - 2026

AI-powered log triage and voice assistant for AWS. Flare pulls CloudWatch logs, identifies anomalous sections using [Cordon](https://github.com/calebevans/cordon), generates a root cause analysis with Amazon Nova, and optionally calls the on-call engineer to walk them through it by phone.

Built on three Amazon Nova foundation models: Nova Embeddings for semantic anomaly detection, Nova 2 Lite for reasoning, and Nova 2 Sonic for speech-to-speech voice conversation.

## How It Works

```
Trigger (CloudWatch Alarm / EventBridge Schedule / Subscription Filter)
    |
    v
AWS Lambda (container image)
    |
    +-- Pulls logs from CloudWatch Logs
    |
    +-- Token budget planner decides: fit raw or reduce?
    |   +-- If reduction needed: Cordon analyzes via Nova Embeddings on Bedrock
    |
    +-- Sends anomalous sections to Nova 2 Lite for root cause analysis
    |
    +-- Publishes triage report to SNS (email, Slack, PagerDuty, etc.)
    |
    +-- Voice pipeline (optional):
        +-- Nova 2 Lite predicts follow-up questions, pre-fetches CloudWatch data
        +-- Amazon Connect calls the on-call engineer
        +-- Nova 2 Sonic delivers the RCA briefing and handles interactive voice investigation
```

### Token Budget System

Flare automatically determines whether logs need reduction. If logs fit within the configured token budget, they go straight to Nova 2 Lite for analysis (no Cordon overhead). If they exceed the budget, Cordon's anomaly percentile is calculated dynamically to hit the target. For example, a 900K token budget with 2M tokens of logs results in keeping the top 45% most anomalous sections.

For multiple log groups, budget is allocated via greedy fair-share: small groups that fit keep their full logs, remaining budget is split proportionally among larger groups.

## Quick Start

### Prerequisites

- AWS account with Bedrock access to Amazon Nova models
- AWS CLI configured with appropriate credentials
- Docker or Podman (for `make setup-image`)

### 1. Copy the image to your ECR

Lambda requires images to be in ECR. This pulls the public image from GHCR and pushes it to an ECR repository in your account:

```bash
make setup-image REGION=us-east-1
```

This prints the `IMAGE_URI` to use in the next step.

### 2. Deploy

Base stack only (email notifications, no voice):

```bash
make deploy \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=oncall@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*,/aws/ecs/my-cluster/*"
```

Base stack with voice pipeline (single command):

```bash
make deploy-all \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=oncall@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*,/aws/ecs/my-cluster/*" \
  ONCALL_PHONE="+15551234567" \
  ENABLE_ALARM=true \
  ALARM_NAME_PREFIX=prod-
```

No triggers are enabled by default. You choose which ones to activate.

### 3. Teardown

```bash
make teardown-all    # removes both voice and base stacks
```

For detailed setup instructions including trigger configuration, notification channels, tuning, and voice pipeline setup, see the [Setup Guide](docs/setup-guide.md).

## Configuration

All configuration is via CloudFormation parameters, which become Lambda environment variables.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ImageUri` | *required* | ECR container image URI (see `make setup-image`) |
| `LogGroupPatterns` | *required* | Log groups or prefix patterns (e.g., `/aws/lambda/*,/my-app/api`) |
| `NotificationEmail` | *required* | Email for SNS alerts |
| `EnableSchedule` | `false` | Run periodic scheduled scans |
| `ScheduleExpression` | `rate(1 hour)` | EventBridge schedule expression |
| `EnableAlarmTrigger` | `false` | Trigger on CloudWatch Alarm state changes |
| `AlarmNamePrefix` | `""` | Only react to alarms matching this prefix |
| `EnableSubscription` | `false` | Attach a CloudWatch Logs subscription filter |
| `SubscriptionLogGroup` | first in list | Which log group to attach the subscription filter to |
| `SubscriptionFilterPattern` | `?ERROR ?FATAL ?CRITICAL` | Filter pattern for subscription trigger |
| `LookbackMinutes` | `30` | Minutes of logs to pull when triggered |
| `TokenBudget` | `0` (auto) | Max input tokens; 0 = model context window |
| `CordonWindowSize` | `4` | Lines per Cordon analysis window |
| `CordonKNeighbors` | `5` | k-NN neighbors for anomaly scoring |
| `BedrockRegion` | `us-east-1` | AWS region for Bedrock API calls |

## Trigger Modes

All three trigger modes can be enabled simultaneously on the same stack. No triggers are enabled by default; you must explicitly enable the ones you want.

**Alarm** (`EnableAlarmTrigger=true`): Fires when CloudWatch Alarms matching `AlarmNamePrefix` enter ALARM state. Best for reactive triage.

**Schedule** (`EnableSchedule=true`): Periodic scans of all configured log groups. Best for routine monitoring. Flare skips the notification on scheduled scans when logs appear healthy.

**Subscription** (`EnableSubscription=true`): Real-time streaming via CloudWatch Logs subscription filter on a specific log group. Triggers immediately when matching log events appear. Best for high-severity keywords like ERROR or FATAL.

## Voice Pipeline

The voice pipeline is deployed as a separate stack (`voice-template.yaml`) or together with the base stack via `make deploy-all`. After generating the RCA:

1. **Pre-fetch**: Nova 2 Lite predicts what the engineer will ask and pre-fetches the relevant CloudWatch metrics, logs, and resource status into a DynamoDB cache
2. **Outbound call**: Amazon Connect calls the on-call engineer's phone (runs in parallel with pre-fetch)
3. **Briefing and investigation**: Nova 2 Sonic (via Lex V2) delivers the RCA briefing and handles the interactive voice conversation, with follow-up answers generated by the retrieve-then-reason pattern

Deploy the voice stack separately if needed:

```bash
make deploy-voice \
  IMAGE_URI=<your-ecr-uri> \
  ONCALL_PHONE="+15551234567" \
  LOG_GROUP_PATTERNS="/aws/lambda/*"
```

This provisions Amazon Connect, a phone number, the Lex V2 bot with Nova 2 Sonic S2S, and the contact flow automatically. See the [Voice Setup Guide](docs/voice-setup-guide.md) for details.

The SNS notification is always sent regardless of whether the voice pipeline is enabled, so the engineer receives the RCA by email or Slack even if the call fails.

For architecture details, see the [Architecture Document](docs/architecture.md).

## Development

### Setup

```bash
pip install -e ".[dev]"
pre-commit install
```

### Unit Tests

```bash
pytest
```

All tests run locally with zero cost using moto and unittest.mock.

### Lint and Type Check

```bash
make lint
```

## Demo

The `demo/` directory contains infrastructure for end-to-end testing with a real ECS service and RDS database:

```bash
make deploy-demo      # deploys VPC, RDS, ECS Fargate service
make break-demo       # revokes RDS security group (simulates network partition)
make fix-demo         # restores RDS security group
make teardown-demo    # removes all demo resources
```

## Architecture

The Lambda handler (`src/flare/handler.py`) orchestrates the full pipeline: fetch logs, plan token budget, optionally reduce via Cordon, send to Nova for triage, publish to SNS, and trigger the voice pipeline.

The Cordon integration (`src/flare/analyzer.py`) uses Nova Embeddings on Bedrock for semantic log anomaly detection. No local model download needed.

Nova 2 Lite (`src/flare/triage.py`) receives the anomalous log sections (or raw logs if they fit) and produces a structured triage report: severity, root cause, affected components, evidence, and next steps.

The predictive pre-fetch (`src/flare/prefetch.py`) asks Nova 2 Lite what CloudWatch metrics and logs the engineer would investigate next, then executes those queries in parallel and caches the results in DynamoDB.

The voice handler (`src/flare/voice_handler.py`) provides a dispatcher with two routes: a briefing handler (reads the RCA for the Connect contact flow to pass to Nova Sonic) and a fulfillment handler (answers follow-up questions using the retrieve-then-reason pattern with cached data and Nova 2 Lite). All voice output is delivered through Nova 2 Sonic speech-to-speech.

For a comprehensive architecture overview with diagrams, see the [Architecture Document](docs/architecture.md).

## License

Apache 2.0
