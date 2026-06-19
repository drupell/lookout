"""Model configuration dataclass — the single interface for specifying LLM behavior.

Node-level configs are loaded from environment variables so that switching
providers is config-only, never a code change.  Env vars:

    LLM_PROVIDER        — "anthropic" | "bedrock" | "openai"
    LLM_SCORING_MODEL   — model id for the scoring node
    LLM_DRAFTING_MODEL  — model id for the email-drafting node
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

_DEFAULT_PROVIDER: Literal["anthropic", "bedrock", "openai"] = "bedrock"
_DEFAULT_SCORING_MODEL = "amazon.nova-lite-v1:0"
_DEFAULT_DRAFTING_MODEL = "amazon.nova-lite-v1:0"


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for an LLM call. All fields are required — no provider defaults."""

    provider: Literal["anthropic", "bedrock", "openai"]
    model_id: str
    temperature: float
    max_tokens: int


def scoring_model_config() -> ModelConfig:
    """Return the ModelConfig for the deal-scoring node.

    Output budget: each scored listing produces ~150 tokens of JSON.
    Nova Lite caps output at 10000 tokens, which fits ~65 listings per call —
    above MarketCheck's 50-row default page. If we ever batch larger, switch
    models or split into multiple scoring calls.
    """
    return ModelConfig(
        provider=os.environ.get("LLM_PROVIDER", _DEFAULT_PROVIDER),  # type: ignore[arg-type]
        model_id=os.environ.get("LLM_SCORING_MODEL", _DEFAULT_SCORING_MODEL),
        temperature=0.1,
        max_tokens=9999,
    )


def drafting_model_config() -> ModelConfig:
    """Return the ModelConfig for the email-drafting node."""
    return ModelConfig(
        provider=os.environ.get("LLM_PROVIDER", _DEFAULT_PROVIDER),  # type: ignore[arg-type]
        model_id=os.environ.get("LLM_DRAFTING_MODEL", _DEFAULT_DRAFTING_MODEL),
        temperature=0.3,
        max_tokens=1024,
    )
