# Flare

AI-powered log triage and voice-driven triage assistant for AWS. Flare autonomously pulls CloudWatch logs, identifies semantically anomalous sections using [Cordon](https://github.com/calebevans/cordon), generates a root cause analysis, and calls the on-call engineer to walk them through it -- powered by three Amazon Nova foundation models on Bedrock.

## How It Works

```
Trigger (CloudWatch Alarm / EventBridge Schedule / Subscription Filter)
    │
    ▼
AWS Lambda (container image)
    │
    ├─► Pulls logs from CloudWatch Logs
    │
    ├─► Token budget planner decides: fit raw or reduce?
    │   └─► If reduction needed: Cordon analyzes via Nova Embeddings on Bedrock
    │
    ├─► Sends anomalous sections to Nova 2 Lite for root cause analysis
    │
    ├─► Publishes triage report to SNS (email, Slack, PagerDuty, etc.)
    │
    └─► Voice pipeline (optional):
        ├─► Nova 2 Lite predicts follow-up questions, pre-fetches CloudWatch data
        ├─► Amazon Connect calls the on-call engineer
        └─► Nova 2 Sonic delivers the RCA briefing and handles interactive voice investigation
```

### Token Budget System

Flare automatically determines whether logs need reduction. If logs fit within the configured token budget, they go straight to Nova 2 Lite for analysis (no Cordon overhead). If they exceed the budget, Cordon's anomaly percentile is calculated dynamically to hit the target -- e.g., 900K budget with 2M tokens of logs results in keeping the top 45% most anomalous sections.

For multiple log groups, budget is allocated via greedy fair-share: small groups that fit keep their full logs, remaining budget is split proportionally among larger groups.

## Quick Start

### Prerequisites

- AWS account with Bedrock access to Amazon Nova models
- AWS CLI configured with appropriate credentials

### Deploy

Using the Makefile:

```bash
make deploy \
  REGION=us-east-1 \
  EMAIL=oncall@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*,/aws/ecs/my-cluster/*"
```

Or directly with CloudFormation:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name flare \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    LogGroupPatterns="/aws/lambda/*,/aws/ecs/my-cluster/*" \
    NotificationEmail=oncall@example.com
```

The template defaults to a pre-built container image from private ECR. No Docker or image building required. No triggers are enabled by default -- you choose which ones to activate.

To enable triggers:

```bash
make deploy \
  REGION=us-east-1 \
  EMAIL=oncall@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*,/aws/ecs/my-cluster/*" \
  ENABLE_SCHEDULE=true \
  SCHEDULE_EXPRESSION="rate(30 minutes)" \
  ENABLE_ALARM=true \
  ENABLE_SUBSCRIPTION=true \
  SUBSCRIPTION_LOG_GROUP=/aws/lambda/my-critical-app \
  SUBSCRIPTION_FILTER="?ERROR ?FATAL"
```

### Teardown

```bash
make teardown          # base stack only
make teardown-voice    # voice stack only
make teardown-all      # both stacks
```

For detailed setup instructions including trigger configuration, notification channels (Slack, PagerDuty), tuning, and voice pipeline setup, see the [Setup Guide](docs/setup-guide.md).

## Configuration

All configuration is via CloudFormation parameters, which become Lambda environment variables.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LogGroupPatterns` | *required* | Log groups or prefix patterns (e.g., `/aws/lambda/*,/my-app/api`) |
| `NotificationEmail` | *required* | Email for SNS alerts |
| `EcrImageUri` | private ECR image | Container image URI (override to use your own build) |
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
| `ConnectEnabled` | `false` | Enable outbound voice calling (auto-provisions Connect, Lex, phone number) |
| `OncallPhone` | `""` | On-call engineer phone number (E.164 format) |

## Trigger Modes

All three trigger modes can be enabled simultaneously on the same stack.

No triggers are enabled by default. You must explicitly enable the ones you want.

**Alarm** (`EnableAlarmTrigger=true`) -- Fires when CloudWatch Alarms matching `AlarmNamePrefix` enter ALARM state. Best for reactive triage: "my CPU alarm fired, what's in the logs?"

**Schedule** (`EnableSchedule=true`) -- Periodic scans of all configured log groups. Best for routine monitoring and audit scanning.

**Subscription** (`EnableSubscription=true`) -- Real-time streaming via CloudWatch Logs subscription filter on a specific log group. Triggers immediately when matching log events appear. Best for high-severity keywords like ERROR or FATAL.

## Voice Pipeline

The voice pipeline is deployed as a separate stack (`voice-template.yaml`). After generating the RCA:

1. **Pre-fetch**: Nova 2 Lite predicts what the engineer will ask and pre-fetches the relevant CloudWatch metrics, logs, and resource status into a DynamoDB cache
2. **Outbound call**: Amazon Connect calls the on-call engineer's phone (runs in parallel with pre-fetch)
3. **Briefing + Investigation**: Nova 2 Sonic (via Lex V2) delivers the RCA briefing and handles the interactive voice conversation -- all speech is powered by Nova Sonic speech-to-speech, with follow-up answers generated by the retrieve-then-reason pattern

Deploy the voice stack after the base stack:

```bash
make deploy-voice \
  ONCALL_PHONE="+15551234567" \
  LOG_GROUP_PATTERNS="/aws/lambda/*"
```

This provisions Amazon Connect, a phone number, the Lex V2 bot with Nova 2 Sonic S2S, and the contact flow automatically. See the [Voice Setup Guide](docs/voice-setup-guide.md) for details.

The SNS notification is always sent regardless of whether the voice pipeline is enabled, so the engineer receives the RCA by email/Slack even if the call fails.

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

### Local Integration Testing (LocalStack)

Run the full pipeline locally against LocalStack with a local embedding model and Gemini for LLM analysis. Requires [podman-compose](https://github.com/containers/podman-compose) (or Docker Compose).

```bash
# 1. Start LocalStack
podman-compose up -d

# 2. Source local environment and seed test data
source .env.local
bash scripts/local_setup.sh

# 3. Set your Gemini API key
export GEMINI_API_KEY=<your-key>

# 4. Invoke the handler
python scripts/local_invoke.py --event schedule
```

This uses `sentence-transformers` for embeddings (runs locally, no API calls) and Gemini for the LLM analysis. All AWS calls (CloudWatch Logs, SNS) go to LocalStack.

To iterate, edit code and re-run step 4. No container rebuild needed.

```bash
# Stop LocalStack when done
podman-compose down
```

## Demo

The `demo/` directory contains resources for end-to-end testing:

```bash
# Deploy the demo failing Lambda
aws cloudformation deploy \
  --template-file demo/demo-template.yaml \
  --stack-name flare-demo \
  --capabilities CAPABILITY_IAM

# Trigger failures and run Flare
./demo/trigger.sh flare-demo-failing flare-<stack-name> 5 mixed
```

## Architecture

**Lambda handler** (`src/flare/handler.py`) orchestrates the full pipeline: fetch logs, plan token budget, optionally reduce via Cordon, send to Nova for triage, publish to SNS, and trigger the voice pipeline.

**Cordon integration** (`src/flare/analyzer.py`) uses the `remote` backend with Nova Embeddings on Bedrock (`bedrock/amazon.nova-2-multimodal-embeddings-v1:0`). No local model download needed.

**Nova 2 Lite** (`src/flare/triage.py`) receives the anomalous log sections (or raw logs if they fit) and produces a structured triage report: severity, root cause, affected components, evidence, and next steps.

**Predictive pre-fetch** (`src/flare/prefetch.py`) asks Nova 2 Lite what CloudWatch metrics and logs the engineer would investigate next, then executes those queries in parallel and caches the results in DynamoDB.

**Voice handler** (`src/flare/voice_handler.py`) provides a dispatcher with two routes: a briefing handler (reads the RCA for the Connect contact flow to pass to Nova Sonic) and a fulfillment handler (answers follow-up questions using the retrieve-then-reason pattern with cached data and Nova 2 Lite). All voice output is delivered through Nova 2 Sonic speech-to-speech.

For a comprehensive architecture overview with diagrams, see the [Architecture Document](docs/architecture.md).

## License

Apache 2.0
