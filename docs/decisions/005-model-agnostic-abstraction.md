# ADR 005: Model-Agnostic LLM Abstraction Layer

## Status

Accepted

## Context

The system makes LLM calls in three distinct contexts:

1. **Deal scoring** — requires high reasoning capability, benefits from the most capable model.
2. **Email drafting** — requires good writing quality but lower complexity than scoring.
3. **Semantic review** — LLM-as-judge pattern, requires consistent evaluation behavior.

Each context has different cost, latency, and capability requirements. The LLM landscape evolves rapidly — new models, pricing changes, and provider outages are regular occurrences.

## Decision

All LLM calls are routed through a single abstraction layer (`src/llm/provider.py`) that accepts a `ModelConfig` dataclass specifying provider, model ID, temperature, and max tokens. No downstream code constructs LLM clients directly. Different graph nodes may intentionally use different models.

## Consequences

### Positive

- **Cost optimization.** The scoring node can use a capable but expensive model (Claude Opus) while the email drafter uses a faster, cheaper model (Claude Sonnet). This is a config change, not a code change.
- **Capability experimentation.** When a new model is released (e.g., GPT-5, Claude 4.5), it can be tested by changing a single config value. A/B testing different models for scoring accuracy requires no code changes.
- **Vendor risk mitigation.** If Anthropic has an outage, switching to OpenAI or Bedrock requires only updating the `ModelConfig`. In a regulated environment, demonstrating a documented fallback path for third-party service dependencies is a risk management expectation.
- **Explicit parameter control.** Every LLM call must specify `temperature` and `max_tokens` — no reliance on provider defaults, which can change between API versions. This prevents silent behavior changes when a provider updates their defaults.
- **Testability.** `TEST_MODE=true` swaps all providers for a `MockChatModel` that returns deterministic fixture responses. The full graph can be tested without any API keys or network access.

### Negative

- **Abstraction cost.** The provider layer adds one level of indirection. In practice, this is a single function call that adds negligible overhead compared to the network round-trip of an LLM API call.
- **Lowest common denominator risk.** The abstraction may not expose provider-specific features (e.g., Anthropic's tool use format, OpenAI's function calling). For this system, the standard chat completion interface is sufficient for all use cases.
- **Configuration management.** Model configs must be maintained per node, per environment. This is managed through the `EnvironmentConfig` dataclass and could be extended to per-node config in the preferences YAML.

## Alternatives Considered

- **Direct provider SDK calls:** Rejected because it couples node implementations to specific providers, making model swaps require code changes across multiple files.
- **LangChain's built-in model routing:** LangChain provides model selection capabilities, but they don't enforce explicit `temperature` and `max_tokens` on every call. The custom abstraction layer adds this enforcement.
- **AWS Bedrock as single provider:** Rejected because it limits model selection to Bedrock-hosted models only. While Bedrock offers Claude and other models, direct API access provides faster model availability and more pricing options.
