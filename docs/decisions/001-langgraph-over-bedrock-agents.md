# ADR 001: LangGraph Over AWS Bedrock Agents

## Status

Accepted

## Context

Lookout requires an orchestration framework to coordinate multiple tool calls (scraping, trade-in lookup, RAG retrieval, LLM scoring, email drafting) into a reliable, auditable pipeline. Two primary options were evaluated:

1. **AWS Bedrock Agents** — a fully managed agent service within the AWS ecosystem.
2. **LangGraph** — an open-source graph-based orchestration framework built on LangChain.

The system must support structural guardrails between every node, a human-in-the-loop trust boundary, full execution auditability, and local testability without AWS credentials.

## Decision

Use LangGraph as the orchestration framework.

## Consequences

### Positive

- **Explicit graph structure.** The entire execution flow is visible in code as a directed graph. Every node, edge, and conditional branch is inspectable and testable — there is no implicit orchestration logic hidden inside a managed service.
- **Guardrail placement control.** Pydantic validation nodes can be inserted between any two nodes in the graph. Bedrock Agents provides guardrails at the input/output boundary only, not between intermediate steps.
- **Framework portability.** LangGraph is cloud-agnostic. The same graph runs locally, in a Lambda, in ECS, or on a developer laptop. Bedrock Agents locks orchestration to AWS.
- **Local testability.** The full graph can be invoked with `TEST_MODE=true` and fixture data, requiring zero AWS credentials. Bedrock Agents requires an active AWS session for any testing.
- **Model agnosticism.** LangGraph nodes can use different LLM providers per node (e.g., Claude for scoring, GPT-4o for drafting). Bedrock Agents is constrained to Bedrock-hosted models.
- **State transparency.** `AgentState` is a plain TypedDict — every field is explicit, typed, and inspectable at any point in the run. Bedrock Agents manages state internally.

### Negative

- **Operational overhead.** We own the Lambda packaging, Docker build, and deployment pipeline. Bedrock Agents handles this as a managed service.
- **No managed action groups.** Bedrock Agents provides built-in integrations for common actions. We must implement and maintain all tool integrations ourselves.
- **Framework dependency.** LangGraph is an open-source project with its own release cadence and breaking change risk, though LangChain's ecosystem is mature and widely adopted.

## Alternatives Considered

- **AWS Bedrock Agents:** Rejected due to lack of inter-node guardrail placement, limited local testability, and vendor lock-in for orchestration logic.
- **AWS Step Functions:** Considered for its visual workflow and native AWS integration, but rejected because it lacks native LLM tool-calling patterns and would require significant custom integration code for each node.
- **Custom orchestration (no framework):** Rejected because it would require reimplementing state management, conditional routing, and error handling that LangGraph provides out of the box.
- **CrewAI / AutoGen:** Rejected because they optimize for multi-agent collaboration patterns, which is unnecessary for this single-agent pipeline. LangGraph's explicit graph model is a better fit for a deterministic, auditable workflow.
