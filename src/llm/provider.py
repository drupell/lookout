"""Model-agnostic LLM abstraction layer.

This is the ONLY place LLM clients are instantiated. All downstream code
receives a configured ChatModel — never constructs one directly.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

if TYPE_CHECKING:
    from src.llm.config import ModelConfig


class MockChatModel(BaseChatModel):
    """Deterministic mock LLM for TEST_MODE. Returns fixture responses."""

    fixture_response: str = '{"result": "mock"}'

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        from langchain_core.outputs import ChatGeneration, ChatResult

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.fixture_response))]
        )


def get_llm(config: ModelConfig) -> BaseChatModel:
    """Return a LangChain ChatModel for the given configuration.

    In TEST_MODE, always returns a MockChatModel regardless of provider.
    Swapping providers in production requires only changing ModelConfig.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return MockChatModel()

    match config.provider:
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=config.model_id,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        case "bedrock":
            from langchain_aws import ChatBedrock

            return ChatBedrock(
                model_id=config.model_id,
                model_kwargs={
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                },
            )
        case "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=config.model_id,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        case _:
            raise ValueError(f"Unsupported LLM provider: {config.provider}")
