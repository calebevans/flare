from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_NOVA_MODEL_ID = "us.amazon.nova-2-lite-v1:0"
_DEFAULT_EMBEDDING_MODEL_ID = "bedrock/amazon.nova-2-multimodal-embeddings-v1:0"


@dataclass(frozen=True, slots=True)
class FlareConfig:
    """Immutable configuration for a Flare invocation.

    All fields map 1:1 to environment variables set by the SAM template.
    Use ``from_env()`` to construct from the Lambda environment.
    """

    log_group_patterns: list[str]
    sns_topic_arn: str
    lookback_minutes: int = 30
    bedrock_region: str = "us-east-1"
    cordon_backend: str = "remote"
    cordon_window_size: int = 4
    cordon_k_neighbors: int = 5
    nova_model_id: str = _DEFAULT_NOVA_MODEL_ID
    embedding_model_id: str = _DEFAULT_EMBEDDING_MODEL_ID
    token_budget: int = 0  # 0 = use model context window
    max_output_tokens: int = 4096
    connect_enabled: bool = False
    oncall_phone: str = ""
    incidents_table_name: str = ""

    @classmethod
    def from_env(cls) -> FlareConfig:
        """Build a FlareConfig from environment variables.

        Raises ``ValueError`` if required variables (``LOG_GROUP_PATTERNS``,
        ``SNS_TOPIC_ARN``) are missing or empty.
        """
        raw_patterns = os.environ.get("LOG_GROUP_PATTERNS", "")
        patterns = [p.strip() for p in raw_patterns.split(",") if p.strip()]
        if not patterns:
            raise ValueError("LOG_GROUP_PATTERNS environment variable is required")

        sns_arn = os.environ.get("SNS_TOPIC_ARN", "")
        if not sns_arn:
            raise ValueError("SNS_TOPIC_ARN environment variable is required")

        return cls(
            log_group_patterns=patterns,
            sns_topic_arn=sns_arn,
            lookback_minutes=int(os.environ.get("LOOKBACK_MINUTES", "30")),
            bedrock_region=os.environ.get("BEDROCK_REGION", "us-east-1"),
            cordon_backend=os.environ.get("CORDON_BACKEND", "remote"),
            cordon_window_size=int(os.environ.get("CORDON_WINDOW_SIZE", "4")),
            cordon_k_neighbors=int(os.environ.get("CORDON_K_NEIGHBORS", "5")),
            nova_model_id=os.environ.get("NOVA_MODEL_ID", _DEFAULT_NOVA_MODEL_ID),
            embedding_model_id=os.environ.get(
                "EMBEDDING_MODEL_ID", _DEFAULT_EMBEDDING_MODEL_ID
            ),
            token_budget=int(os.environ.get("TOKEN_BUDGET", "0")),
            max_output_tokens=int(os.environ.get("MAX_OUTPUT_TOKENS", "4096")),
            connect_enabled=os.environ.get("CONNECT_ENABLED", "").lower() == "true",
            oncall_phone=os.environ.get("ONCALL_PHONE", ""),
            incidents_table_name=os.environ.get("INCIDENTS_TABLE_NAME", ""),
        )

    @property
    def litellm_model(self) -> str:
        """Return the model ID formatted for litellm.

        If ``nova_model_id`` already contains a provider prefix (e.g.
        ``gemini/gemini-2.5-flash``), it is returned as-is.  Otherwise
        ``bedrock/`` is prepended for Bedrock routing.
        """
        if "/" in self.nova_model_id:
            return self.nova_model_id
        return f"bedrock/{self.nova_model_id}"
