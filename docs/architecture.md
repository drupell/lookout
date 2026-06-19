# Lookout — System Architecture

**Prepared for:** Risk Committee / Architecture Review
**Classification:** Internal
**Last Updated:** 2026-04-05

---

## System Purpose

Lookout is an autonomous agent that monitors the used and new electric vehicle market for exceptional deals, scores them against a configurable preference profile, and produces draft outreach emails for human review. It is designed as a personal tool with enterprise-grade architectural patterns appropriate for demonstrating competency in a regulated-industry context.

## Architecture Summary

The system is a **serverless, event-driven agent** deployed on AWS. An EventBridge schedule triggers a Lambda function that executes a LangGraph StateGraph — a directed graph of tool-calling, validation, and decision nodes. Every node output passes through a Pydantic structural guardrail before the graph advances. The agent produces draft emails but is architecturally prohibited from sending them (human-in-the-loop boundary). Every run produces a structured audit record in DynamoDB.

## Component Overview

| Component | Technology | Purpose |
|---|---|---|
| Orchestration | LangGraph (StateGraph) | Directed graph of agent nodes with explicit state |
| LLM Abstraction | Custom provider layer | Model-agnostic interface; swap providers via config |
| Structural Guardrails | Pydantic v2 | Schema + semantic validation at every node boundary |
| Semantic Guardrails | LLM-as-judge | Content review before any outbound action |
| Data Retrieval | Playwright, BeautifulSoup | EV listing and trade-in value scraping |
| RAG | FAISS + LangChain | Tax credit and incentive context retrieval |
| Persistence | DynamoDB (3 tables) | Audit logs, deal history, trade-in time series |
| Secrets | AWS Secrets Manager | API keys and webhook URLs; loaded at cold start |
| Scheduling | EventBridge | Cron/rate-based Lambda invocation |
| Monitoring | CloudWatch + SNS | Dashboard, error/duration alarms, email alerts |
| CI/CD | GitHub Actions + OIDC | Automated pipeline with manual prod approval gate |
| Infrastructure | AWS CDK (Python) | All resources defined as code; zero console operations |

## Trust Boundaries

1. **LLM Output Boundary.** Every LLM response is parsed through a Pydantic model before it enters the agent state. Malformed or out-of-range outputs are rejected and logged as guardrail triggers.

2. **Outbound Communication Boundary.** The agent cannot send email or any outbound communication. It produces `EmailDraft` objects that are delivered to the owner via Slack notification for manual review. This is enforced architecturally (no send/smtp/post calls in the email module) and validated in the eval suite.

3. **Secrets Boundary.** API keys are stored in Secrets Manager and loaded at Lambda cold start. They are never passed as plaintext environment variables, committed to code, or logged.

4. **Environment Boundary.** Dev and prod are separate CDK stacks with different configurations. `TEST_MODE=true` disables all real scraping and external API calls. Environment switching is config-only.

## Data Flow

```
EventBridge Schedule
       │
       ▼
Lambda Handler (cold start: load secrets)
       │
       ▼
┌─────────────────────────────────────────────┐
│              LangGraph StateGraph           │
│                                             │
│  fetch_listings → [guardrail] →             │
│  fetch_trade_in → [guardrail] →             │
│  rag_enrich     → [guardrail] →             │
│  score_deals    → [guardrail] →             │
│  filter_new_deals →                         │
│    ├─ no deals → persist_run → END          │
│    └─ deals   → draft_emails →              │
│                  [semantic guardrail] →      │
│                  persist_results → END       │
└─────────────────────────────────────────────┘
       │
       ▼
DynamoDB (runs, deals, trade-in tables)
       │
       ▼
Slack Notification (deal summary + drafts for review)
```

## Security Controls

| Control | Implementation |
|---|---|
| No wildcard IAM resources | Every policy statement names explicit ARNs |
| No long-lived credentials | OIDC federation for CI/CD; Secrets Manager for runtime |
| Least-privilege Lambda role | Explicit enumeration of DynamoDB actions and table ARNs |
| Input validation | Pydantic guardrails at every node boundary |
| Output review | LLM-as-judge semantic guardrail before persistence |
| Human-in-the-loop | Architectural prohibition on autonomous email sending |
| Audit trail | Structured run records with node-level execution detail |
| Environment separation | Dev/prod as separate CDK stacks with enforced config differences |

## Failure Modes

| Failure | Mitigation |
|---|---|
| Scraper returns malformed data | Pydantic guardrail rejects invalid listings; run ends with GUARDRAIL_BLOCKED status |
| LLM returns unparseable JSON | JSON parse failure caught; logged; node returns empty result |
| LLM scores a bad deal highly | Semantic guardrail reviews drafts; human reviews before sending |
| DynamoDB write fails | Lambda retries (built-in); error logged in CloudWatch; alarm triggers |
| Secrets Manager unavailable | Lambda fails at cold start; CloudWatch error alarm fires |
| All scrapers fail | Empty listings pass guardrail (min_length=0); run completes with 0 deals |

## Cost Model (Estimated Monthly — Personal Use)

| Resource | Estimated Cost |
|---|---|
| Lambda (1 invocation/day, ~60s) | $0.01 |
| DynamoDB (on-demand, <100 writes/day) | $0.01 |
| Secrets Manager (2 secrets) | $0.80 |
| CloudWatch Logs + Alarms | $0.50 |
| LLM API calls (1 scoring + 1 drafting/day) | $1-5 |
| **Total** | **~$2-7/month** |

## Decision Records

All significant architectural decisions are documented as ADRs in `docs/decisions/`:

- [001: LangGraph over Bedrock Agents](decisions/001-langgraph-over-bedrock-agents.md)
- [002: DynamoDB Schema Design](decisions/002-dynamodb-schema-design.md)
- [003: Pydantic Guardrails per Node](decisions/003-pydantic-guardrails-per-node.md)
- [004: OIDC over Static Credentials](decisions/004-oidc-over-static-credentials.md)
- [005: Model-Agnostic Abstraction](decisions/005-model-agnostic-abstraction.md)
- [006: Human-in-the-Loop Email Boundary](decisions/006-hitl-email-boundary.md)
