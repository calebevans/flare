.PHONY: deploy deploy-voice deploy-all teardown teardown-voice teardown-all test lint

STACK_NAME := flare
REGION     ?= us-east-1

# Required
EMAIL          ?=
LOG_GROUP_PATTERNS ?=

# Triggers (all default to template defaults if not set)
ENABLE_SCHEDULE     ?=
SCHEDULE_EXPRESSION ?=
ENABLE_ALARM        ?=
ALARM_NAME_PREFIX   ?=
ENABLE_SUBSCRIPTION ?=
SUBSCRIPTION_LOG_GROUP ?=
SUBSCRIPTION_FILTER ?=

# Analysis
LOOKBACK_MINUTES ?=
TOKEN_BUDGET     ?=

# Voice
ONCALL_PHONE         ?=
CONNECT_INSTANCE_ID  ?=

# Container image (default :latest; override to pin version)
ECR_IMAGE_URI ?= 019107478361.dkr.ecr.us-east-1.amazonaws.com/flare:v0.1.7

define check_param
$(if $($(1)),,$(error $(1) is required. Usage: make deploy $(1)=<value>))
endef

# Build the --parameter-overrides string, only including params that are set
OVERRIDES := EcrImageUri=$(ECR_IMAGE_URI)
ifneq ($(LOG_GROUP_PATTERNS),)
	OVERRIDES += LogGroupPatterns=$(LOG_GROUP_PATTERNS)
endif
ifneq ($(EMAIL),)
	OVERRIDES += NotificationEmail=$(EMAIL)
endif
ifneq ($(ENABLE_SCHEDULE),)
	OVERRIDES += EnableSchedule=$(ENABLE_SCHEDULE)
endif
ifneq ($(SCHEDULE_EXPRESSION),)
	OVERRIDES += ScheduleExpression="$(SCHEDULE_EXPRESSION)"
endif
ifneq ($(ENABLE_ALARM),)
	OVERRIDES += EnableAlarmTrigger=$(ENABLE_ALARM)
endif
ifneq ($(ALARM_NAME_PREFIX),)
	OVERRIDES += AlarmNamePrefix=$(ALARM_NAME_PREFIX)
endif
ifneq ($(ENABLE_SUBSCRIPTION),)
	OVERRIDES += EnableSubscription=$(ENABLE_SUBSCRIPTION)
endif
ifneq ($(SUBSCRIPTION_LOG_GROUP),)
	OVERRIDES += SubscriptionLogGroup=$(SUBSCRIPTION_LOG_GROUP)
endif
ifneq ($(SUBSCRIPTION_FILTER),)
	OVERRIDES += SubscriptionFilterPattern="$(SUBSCRIPTION_FILTER)"
endif
ifneq ($(LOOKBACK_MINUTES),)
	OVERRIDES += LookbackMinutes=$(LOOKBACK_MINUTES)
endif
ifneq ($(TOKEN_BUDGET),)
	OVERRIDES += TokenBudget=$(TOKEN_BUDGET)
endif

deploy:
	$(call check_param,EMAIL)
	$(call check_param,LOG_GROUP_PATTERNS)
	aws cloudformation deploy \
		--template-file template.yaml \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(OVERRIDES)
	@echo "Done. Check your email to confirm the SNS subscription."

deploy-voice:
	$(call check_param,ONCALL_PHONE)
	$(call check_param,LOG_GROUP_PATTERNS)
	$(eval VOICE_OVERRIDES := BaseStackName=$(STACK_NAME) OncallPhone=$(ONCALL_PHONE) LogGroupPatterns=$(LOG_GROUP_PATTERNS) EcrImageUri=$(ECR_IMAGE_URI))
ifneq ($(CONNECT_INSTANCE_ID),)
	$(eval VOICE_OVERRIDES += ConnectInstanceId=$(CONNECT_INSTANCE_ID))
endif
	aws cloudformation deploy \
		--template-file voice-template.yaml \
		--stack-name $(STACK_NAME)-voice \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(VOICE_OVERRIDES)
	@echo "Warming up voice handler Lambda (cold start takes ~10s)..."
	@aws lambda invoke --function-name flare-voice-$(STACK_NAME) --payload '{}' /dev/null --region $(REGION) 2>/dev/null || true
	@echo "Configuring Lex bot with Nova Sonic S2S, fulfillment, and Connect associations..."
	@INSTANCE_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`FlareConnectInstanceArn`].OutputValue' --output text) && \
	BOT_ALIAS_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`FlareBotAliasArn`].OutputValue' --output text) && \
	BOT_ID=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`FlareBotId`].OutputValue' --output text) && \
	LAMBDA_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`FlareVoiceHandlerArn`].OutputValue' --output text) && \
	ALIAS_ID=$$(echo "$$BOT_ALIAS_ARN" | grep -o '[^/]*$$') && \
	aws lexv2-models update-bot-locale --bot-id "$$BOT_ID" --bot-version DRAFT --locale-id en_US \
		--nlu-intent-confidence-threshold 0.4 \
		--unified-speech-settings '{"speechFoundationModel":{"modelArn":"arn:aws:bedrock:$(REGION)::foundation-model/amazon.nova-2-sonic-v1:0"}}' \
		--region $(REGION) > /dev/null && \
	aws lexv2-models build-bot-locale --bot-id "$$BOT_ID" --bot-version DRAFT --locale-id en_US \
		--region $(REGION) > /dev/null && \
	sleep 20 && \
	NEW_VER=$$(aws lexv2-models create-bot-version --bot-id "$$BOT_ID" \
		--bot-version-locale-specification '{"en_US":{"sourceBotVersion":"DRAFT"}}' \
		--region $(REGION) --query 'botVersion' --output text) && \
	sleep 10 && \
	aws lexv2-models update-bot-alias --bot-id "$$BOT_ID" --bot-alias-id "$$ALIAS_ID" \
		--bot-alias-name live --bot-version "$$NEW_VER" \
		--bot-alias-locale-settings '{"en_US":{"enabled":true,"codeHookSpecification":{"lambdaCodeHook":{"lambdaARN":"'"$$LAMBDA_ARN"'","codeHookInterfaceVersion":"1.0"}}}}' \
		--region $(REGION) > /dev/null && \
	aws connect associate-bot --instance-id "$$INSTANCE_ARN" \
		--lex-v2-bot AliasArn="$$BOT_ALIAS_ARN" --region $(REGION) 2>/dev/null || true
	@echo "Updating base stack to enable voice..."
	@aws cloudformation deploy \
		--template-file template.yaml \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(OVERRIDES) ConnectEnabled=true OncallPhone=$(ONCALL_PHONE)
	@echo "Voice pipeline active. Your phone will ring on incidents."

deploy-all: deploy deploy-voice

teardown-voice:
	aws cloudformation delete-stack --stack-name $(STACK_NAME)-voice --region $(REGION)
	@echo "Voice stack deletion initiated."

teardown:
	aws cloudformation delete-stack --stack-name $(STACK_NAME) --region $(REGION)
	@echo "Base stack deletion initiated."

teardown-all: teardown-voice
	@echo "Waiting for voice stack to delete before removing base stack..."
	aws cloudformation wait stack-delete-complete --stack-name $(STACK_NAME)-voice --region $(REGION) 2>/dev/null || true
	aws cloudformation delete-stack --stack-name $(STACK_NAME) --region $(REGION)
	@echo "All stacks deletion initiated."

test:
	pytest -v

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src/flare/
