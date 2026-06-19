# ADR 002: DynamoDB Schema Design — Three Tables Over Single-Table

## Status

Accepted

## Context

The system requires persistent storage for three distinct data domains:

1. **Run audit logs** — one record per agent invocation, keyed by `run_id`.
2. **Deal history** — scored deals with status tracking, keyed by `listing_id`.
3. **Trade-in time series** — periodic trade-in value snapshots, keyed by `vin` + `date`.

DynamoDB single-table design (STD) is a well-known pattern that consolidates all entities into one table using composite keys and GSIs. The alternative is separate tables per entity.

## Decision

Use three separate DynamoDB tables, each with on-demand (pay-per-request) billing.

## Consequences

### Positive

- **Schema clarity.** Each table has a self-documenting key structure. `lookout-runs` has `run_id/timestamp`, `lookout-deals` has `listing_id/first_seen`, `lookout-trade-in` has `vin/date`. No composite key encoding/decoding required.
- **Independent scaling.** Each table scales independently. The runs table may have high write throughput during testing while the deals table remains quiet. On-demand billing means we pay only for what we use.
- **Simpler IAM.** Lambda permissions can be scoped to specific table ARNs. With STD, a single table ARN grants access to all data domains.
- **Easier backup/restore.** Point-in-time recovery operates per-table. Restoring deal history does not require filtering out run logs.
- **Removal policy independence.** The prod stack uses `RemovalPolicy.RETAIN` on all tables, but if requirements changed, each table could have its own lifecycle policy.

### Negative

- **More CDK resources.** Three tables means three sets of GSIs, three IAM resource ARNs, and three environment variables. This is manageable at project scale.
- **No cross-entity transactions.** DynamoDB transactions work within a single table. Cross-table operations (e.g., writing a deal and updating the run record atomically) require application-level consistency. For this workload, eventual consistency is acceptable — the run record is written after all deals.
- **Diverges from DynamoDB best practices at scale.** At enterprise scale with hundreds of entity types, STD reduces table proliferation. At project scale with three entities, the simplicity benefit of separate tables outweighs the STD pattern.

### On-Demand vs. Provisioned Billing

On-demand billing was chosen because:
- The workload is highly intermittent (1-2 invocations per day).
- There is no baseline traffic to justify provisioned capacity.
- On-demand eliminates the risk of throttling during development/testing spikes.
- At this scale, on-demand cost is negligible ($0.25/million writes, $0.25/million reads).

Provisioned capacity would only be cost-effective if the system ran continuously at predictable throughput, which is not the case for a scheduled daily agent.

## Alternatives Considered

- **Single-table design:** Rejected for this workload because it adds key encoding complexity without meaningful scaling benefit at personal project scale. The three data domains have fundamentally different access patterns (audit queries vs. dedup lookups vs. time-series scans) that are more naturally expressed as separate tables.
- **PostgreSQL (RDS/Aurora):** Rejected because it introduces a continuously running instance cost ($15-50/month minimum), requires VPC configuration for Lambda access, and adds operational overhead disproportionate to the data volume.
- **S3 + Athena:** Considered for the audit log (append-only, query-rarely pattern), but rejected because DynamoDB's point-in-time recovery and GSI queries provide sufficient capability without adding another service.
