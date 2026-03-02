# Voice Setup Guide

Flare's voice pipeline -- Amazon Connect, Lex V2 with Nova 2 Sonic speech-to-speech, and the contact flow -- is deployed as a separate CloudFormation stack (`voice-template.yaml`). All voice interactions use Nova 2 Sonic S2S. The Makefile handles provisioning, Nova Sonic configuration, and wiring automatically.

**Region**: All resources are created in the deployment region. Nova 2 Sonic requires **us-east-1** or **us-west-2**.

**Time estimate**: ~5 minutes (deploy command + wait for provisioning).

---

## Prerequisites

1. **Deploy the base Flare stack first** (see [Setup Guide](setup-guide.md))

2. **Enable Nova model access in Bedrock**

   Open the [Amazon Bedrock console](https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess) in us-east-1 and enable:
   - Amazon Nova 2 Lite (log analysis + reasoning)
   - Amazon Nova Multimodal Embeddings (Cordon anomaly detection)
   - Amazon Nova 2 Sonic (voice conversation)

   Click **Save changes** and wait for "Access granted" status.

3. **AWS CLI configured** with credentials that have permissions to create CloudFormation stacks, Connect instances, and Lex bots.

---

## Deploy

```bash
make deploy-voice \
  ONCALL_PHONE="+15551234567" \
  LOG_GROUP_PATTERNS="/aws/lambda/*"
```

This single command handles everything with zero manual steps:

1. Deploys the `voice-template.yaml` CloudFormation stack (Connect, Lex bot, DynamoDB, Lambda, contact flow)
2. Warms up the voice handler Lambda (eliminates cold start on first call)
3. Enables Nova 2 Sonic S2S on the Lex bot locale
4. Builds the bot locale and waits for the build to complete (polled, not timed)
5. Creates a new bot version and waits for it to become available (polled, not timed)
6. Updates the live bot alias to the new version with the fulfillment Lambda code hook
7. Associates the Lex bot with the Connect instance
8. Updates the base stack to enable the voice pipeline (`ConnectEnabled=true`)

Each async step (locale build, version creation) is polled until complete, with clear progress messages and failure detection. No manual CLI commands or console steps are needed.

| Resource | What it creates |
|----------|----------------|
| Amazon Connect instance | Outbound calling enabled, managed identity |
| US DID phone number | Claimed automatically, used as caller ID |
| Lex V2 bot | FlareTriage with Nova 2 Sonic S2S, intents, and utterances |
| Contact flow | Set Voice (Generative), Lambda invoke, Nova Sonic briefing + conversation |
| DynamoDB table | Incident state and pre-fetched investigation cache |
| SSM parameter | Stores Connect config (instance ID, flow ID, phone number) for the base stack |
| Lambda permissions | Connect and Lex can invoke the voice handler |

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ONCALL_PHONE` | *required* | Engineer's phone number in E.164 format (e.g., `+15551234567`) |
| `LOG_GROUP_PATTERNS` | *required* | Must match the base stack's log group patterns |
| `CONNECT_INSTANCE_ID` | `""` | Reuse an existing Connect instance (leave empty to create one) |
| `ECR_IMAGE_URI` | private ECR (pinned in Makefile) | Container image URI (should match the base stack) |

---

## Deploy Both Stacks at Once

```bash
make deploy-all \
  EMAIL=oncall@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*" \
  ENABLE_ALARM=true \
  ALARM_NAME_PREFIX=prod- \
  ONCALL_PHONE="+15551234567"
```

This deploys the base stack first, then the voice stack.

---

## Verify

After deployment, check the voice stack outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name flare-voice \
  --query 'Stacks[0].Outputs' \
  --output table
```

You should see:
- `FlareConnectInstanceId` -- the provisioned Connect instance
- `FlarePhoneNumber` -- the claimed DID number
- `FlareBotId` -- the Lex bot ID
- `FlareVoiceHandlerArn` -- the voice Lambda function

Verify Nova Sonic is active:

```bash
aws lexv2-models describe-bot-locale \
  --bot-id <FlareBotId> \
  --bot-version <latest-version> \
  --locale-id en_US \
  --region us-east-1 \
  --query 'unifiedSpeechSettings'
```

You should see `speechFoundationModel.modelArn` pointing to `amazon.nova-2-sonic-v1:0`.

---

## Test

Trigger a test incident using the demo resources:

```bash
# Deploy the demo failing Lambda (if not already deployed)
aws cloudformation deploy \
  --template-file demo/demo-template.yaml \
  --stack-name flare-demo \
  --capabilities CAPABILITY_IAM

# Generate errors and wait for the alarm to fire
bash demo/trigger.sh
```

Watch the logs:

```bash
# Main analysis Lambda
aws logs tail /aws/lambda/flare-flare --follow --region us-east-1

# Voice handler Lambda
aws logs tail /aws/lambda/flare-voice-flare --follow --region us-east-1
```

Your phone should ring within ~30 seconds of the alarm firing. Nova Sonic delivers the RCA briefing and then listens for your follow-up questions.

---

## How the Voice Flow Works

1. **Alarm fires** -- EventBridge triggers the Flare Lambda
2. **Analysis** -- Cordon + Nova Embeddings reduce logs, Nova 2 Lite generates RCA
3. **Pre-fetch** -- Nova 2 Lite predicts follow-up questions, CloudWatch data is cached in DynamoDB
4. **Outbound call** -- Amazon Connect calls the on-call engineer (parallel with pre-fetch)
5. **Briefing** -- Contact flow invokes the briefing Lambda to fetch the RCA, then hands off to the Lex bot. Nova 2 Sonic delivers the RCA as its opening statement.
6. **Conversation** -- The engineer asks questions. Lex classifies intents, the fulfillment Lambda retrieves data (cache-first, live fallback), Nova 2 Lite reasons about it, and Nova 2 Sonic speaks the answer.

All voice output goes through Nova 2 Sonic speech-to-speech. No separate TTS engine is used.

---

## Teardown

```bash
make teardown-voice    # voice stack only
make teardown-all      # both stacks
```

This deletes the voice CloudFormation stack, which removes the Connect instance, phone number, Lex bot, DynamoDB table, voice handler Lambda, and all associated IAM roles. No lingering charges.

---

## Troubleshooting

### "CREATE_FAILED on FlareConnectInstance"

- Connect instance creation is rate-limited (limited operations per 30-day window). If you've been creating/deleting instances frequently, wait and retry.
- The instance alias must be globally unique. If `flare-{stack-name}` is taken, change the stack name.
- To reuse an existing instance: `make deploy-voice CONNECT_INSTANCE_ID=<instance-id> ...`

### "CREATE_FAILED on FlarePhoneNumber"

- DID number availability varies by region. The template requests a US DID; if none are available, the stack will roll back. Retry or try a different region.

### Voice handler returns "sorry technical issue"

- Check the voice handler Lambda logs: `aws logs tail /aws/lambda/flare-voice-flare --region us-east-1`
- Verify the Lex bot has Lambda invoke permission: `aws lambda get-policy --function-name flare-voice-flare`
- Verify the bot alias points to a version with Nova Sonic enabled

### Fulfillment Lambda timeout

- Check that the DynamoDB table has incident records with `prefetch_status: complete`
- Check CloudWatch Logs for the voice handler Lambda for errors
- The fulfillment Lambda has an 8-second Lex limit; cache hits take ~3s, cache misses ~5-6s

### Nova Sonic not active

- Ensure Nova 2 Sonic model access is enabled in Bedrock
- `make deploy-voice` configures Nova Sonic, builds the locale, creates a versioned snapshot, and updates the alias automatically. If the deploy was interrupted mid-way, re-run `make deploy-voice` -- it is idempotent.
- Verify with: `aws lexv2-models describe-bot-locale --bot-id <id> --bot-version <ver> --locale-id en_US --query 'unifiedSpeechSettings'`
