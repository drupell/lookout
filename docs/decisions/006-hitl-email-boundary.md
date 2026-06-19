# ADR 006: Human-in-the-Loop Email Boundary

## Status

Accepted

## Context

The Lookout agent identifies qualifying deals and can generate outreach emails to dealerships. The question is whether the agent should be permitted to send these emails autonomously or whether a human must review and approve each email before it is sent.

This decision is evaluated from both a practical risk perspective (an incorrect email damages the buyer's negotiating position) and a responsible AI perspective (autonomous outbound communication by an AI system carries reputational and legal risk).

## Decision

The agent is architecturally prohibited from sending any email or outbound communication. The email module (`src/tools/email/drafter.py`) produces `EmailDraft` objects only. There are no `send`, `post`, `smtp`, or `requests` calls anywhere in the email module. This is enforced at the code level, validated in evals, and documented as a non-negotiable constraint.

Draft emails are delivered to the human owner through the notification channel (Slack/SES summary) for review. The human decides whether to send, modify, or discard each draft.

## Consequences

### Positive

- **Trust boundary.** The agent cannot take irreversible actions on behalf of the user. An incorrect deal assessment or an inappropriately worded email is caught at the review stage, not after it has been sent to a dealer.
- **Semantic guardrail defense in depth.** Even though the LLM-as-judge semantic review checks drafts for price commitments and misrepresentation, the HITL boundary provides a second layer. The semantic guardrail is a safety net, not the sole control.
- **Legal protection.** An email sent by an AI system could be construed as a binding offer or commitment depending on jurisdiction. Human review ensures that no communication is sent that the user has not personally approved.
- **Negotiation strategy preservation.** The user may want to adjust tone, timing, or specific language based on context the agent does not have (e.g., prior conversations with a dealer, intent to visit in person). Human review preserves this flexibility.
- **Regulatory alignment.** In regulated industries, autonomous AI communication with external parties requires additional controls (approval workflows, content review, retention policies). The HITL boundary satisfies this requirement by design.

### Negative

- **Reduced automation.** The user must manually review and send each email, which reduces the system's end-to-end automation. For a system designed around "passive deal monitoring," this is acceptable — the agent does the research, the human does the outreach.
- **Notification fatigue risk.** If the system generates many drafts, the user may develop notification fatigue and stop reviewing them. This is mitigated by the scoring threshold — only exceptional deals trigger draft generation.
- **Delay between detection and action.** A time-sensitive deal could expire between when the agent identifies it and when the user reviews the draft. For the target use case (passive monitoring, not time-critical arbitrage), this trade-off is acceptable.

## Alternatives Considered

- **Autonomous sending with guardrails only:** Rejected. Even with semantic review, the risk of an AI sending inappropriate outbound communication is too high for a system designed to demonstrate responsible AI patterns. A single embarrassing email undermines the portfolio value of the entire project.
- **Autonomous sending with approval queue:** Considered — the agent would queue emails and a separate approval service would release them after N hours if not vetoed. Rejected because it inverts the control model (human must act to prevent, rather than act to approve) and is more complex than necessary for a personal project.
- **No email drafting at all:** Rejected because the email drafting capability demonstrates the LLM-as-judge pattern, the HITL trust boundary, and end-to-end agent utility. Removing it would reduce the project's portfolio value.
