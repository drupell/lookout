# ADR 003: Pydantic Guardrails at Every Node Boundary

## Status

Accepted

## Context

The agent graph has 8+ nodes, each producing structured output consumed by downstream nodes. Data quality issues — malformed scraper output, out-of-range scores, missing fields — can propagate through the graph, producing incorrect results or runtime errors far from the original fault.

Two validation strategies were considered:

1. **Terminal-only validation:** Validate only the final output before notification/persistence.
2. **Per-node validation:** Validate every node's output before the next node executes.

## Decision

Every node output must pass through a registered Pydantic v2 structural guardrail before the next node in the graph can execute. The guardrail registry enforces completeness at graph startup — if a node has no registered guardrail and is not explicitly exempt, the graph fails to build.

## Consequences

### Positive

- **Fail-fast behavior.** A malformed listing from the scraper is caught immediately after `fetch_listings`, not after it has been scored, drafted, and persisted. The guardrail trigger is logged with the exact node and error, making debugging straightforward.
- **Auditability.** Every node's guardrail result (PASS/FAIL) is recorded in the structured run record. A compliance reviewer can verify that every stage was validated.
- **Schema as documentation.** The Pydantic models in `structural.py` serve as living documentation of every node's output contract. New developers can read the models to understand the data flow without tracing through node implementations.
- **Registry enforcement.** The `verify_registry_completeness()` check at graph startup means it's impossible to add a new node without defining its guardrail (or explicitly exempting it). This prevents silent validation gaps.
- **Composability.** Each guardrail is an independent function that can be unit-tested in isolation with fixture data, without invoking the full graph.

### Negative

- **Latency overhead.** Each validation adds a small amount of processing time. With 8 guardrail checks per run, this adds ~5-10ms total — negligible relative to the LLM calls and scraping operations that dominate run time.
- **Development friction.** Adding a new node requires updating both the node implementation and the guardrail registry. This is intentional — the friction is a feature, not a bug, because it forces explicit thinking about output contracts.
- **Rigidity.** If a node's output schema changes, the guardrail must be updated simultaneously. In practice, this is managed by having both the node and guardrail in the same codebase with tests that enforce consistency.

## Alternatives Considered

- **Terminal-only validation:** Rejected because errors propagate silently through intermediate nodes, making root cause analysis difficult. In a regulated context, the inability to prove that intermediate outputs were valid is a finding.
- **Runtime type checking (beartype/typeguard):** Considered as a lighter-weight alternative, but rejected because it validates types only, not semantic constraints (e.g., "selling price must be within 15% of MSRP"). Pydantic v2 model validators handle both structural and semantic validation.
- **JSON Schema validation:** Rejected because it lacks Pydantic's model validators for cross-field constraints and produces less actionable error messages.
