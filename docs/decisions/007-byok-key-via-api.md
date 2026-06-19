# ADR 007: Accepting BYOK API Keys Through the Authenticated API

## Status

Accepted

## Context

Lookout's BYOK ("Bring Your Own Key") tier lets users supply their own MarketCheck API key so their runs are billed against their own MarketCheck account, not the shared default-tier quota. The Users table already has a `marketcheck_secret_arn` field; the open question is *how* the user's key gets there.

Two options were considered:

1. **Out-of-band provisioning.** The user emails their key to an admin; the admin uses the AWS CLI to create the secret and update the Users row. This is what Phase 1A shipped — fast to build, but it does not scale beyond a handful of users and forces every BYOK upgrade through manual ops.
2. **Self-service via the API.** The user submits their key through the dashboard. The API Lambda validates the key, stores it in Secrets Manager, and updates the Users row. The key is never persisted in DynamoDB, logs, or returned by any GET endpoint.

Until this decision, the API has never accepted a secret over the wire. The threat model needs to be made explicit before we cross that line.

## Decision

The dashboard accepts BYOK API keys through two new authenticated endpoints:

- `PUT /me/byok-key` — body `{ "api_key": "..." }`. The API Lambda makes one validation call to MarketCheck with the supplied key. On success, it upserts the key to a per-user secret named `lookout/byok/<user_id>` in Secrets Manager, sets `marketcheck_secret_arn` and `tier="byok"` on the Users row, and strips any incompatible BYOK-only `prefs_overrides` is *not* required (overrides are only added on tier upgrade — the demote path is what scrubs them, see below).
- `DELETE /me/byok-key` — deletes the secret with `ForceDeleteWithoutRecovery=True`, clears `marketcheck_secret_arn`, demotes `tier` to `"default"`, and scrubs BYOK-only paths (`schedule.*`, `scoring.*`, `search.target_listings`, `search.max_pages`) from `prefs_overrides` so a demoted user cannot continue benefiting from settings they're no longer entitled to.

The API Lambda's IAM role is granted `secretsmanager:CreateSecret`, `PutSecretValue`, `UpdateSecret`, `DescribeSecret`, and `DeleteSecret` scoped to `arn:aws:secretsmanager:<region>:<acct>:secret:lookout/byok/*` only — never `*` and never the shared default-tier secret (which the API only needs `GetSecretValue` on, for completeness).

The agent worker's IAM role is extended with `secretsmanager:GetSecretValue` on the same `lookout/byok/*` ARN pattern, so per-user runs can fetch the user's own key at runtime instead of falling back to the shared one.

## Threat model

| Threat | Mitigation |
|---|---|
| Key intercepted in transit | API Gateway is TLS-only (HTTPS enforced; no HTTP listener). The dashboard ships from CloudFront over HTTPS. |
| Key logged in CloudWatch | The API handler logs path + method + user only; the request body is never logged. `boto3.put_secret_value` does not log the value. Lambda execution logs are retained 7d (dev) / 90d (prod). |
| Key returned by a GET endpoint | `/me`, `/me/prefs`, and any future endpoint return only a boolean `marketcheck_secret_configured` — never the ARN, never the key value. The ARN itself is stored in DynamoDB but is not surfaced to the client; treating ARNs as semi-sensitive (they reveal the account ID). |
| Key stolen via XSS in the dashboard | Inputs are React-managed (no `dangerouslySetInnerHTML`); CloudFront sets `X-Content-Type-Options`, `X-Frame-Options`, and CSP-adjacent headers via the SECURITY_HEADERS managed policy. The form is a `<input type="password">` so the value is masked in the DOM. |
| Cross-user secret access | Secret names embed the Cognito `sub`; IAM policy is wildcarded by name pattern but the Lambda code only ever derives the secret name from the JWT claims, so a user cannot read or overwrite another user's secret. |
| Operator viewing the secret | Secrets Manager access is gated by IAM. The dev admin profile can read all secrets — acceptable for the dev account; in prod, restrict by SCPs and audit via CloudTrail. |
| Validation bypass | The validation step makes a real MarketCheck request and rejects anything that returns 401/403. A user cannot store a malformed or stolen key that doesn't actually work without immediately discovering it. |

## Consequences

### Positive

- **Self-service onboarding.** BYOK upgrades no longer require admin intervention; a user submits their key and the upgrade lands in seconds.
- **Defense-in-depth IAM.** Per-user secret naming + name-pattern IAM policies prevent cross-user reads even if the Lambda code had a bug. The blast radius of a compromised user is limited to their own key.
- **Validation surfaces errors immediately.** A wrong key is rejected at submission time with a clear UI error, instead of silently producing 0-result runs.
- **Tier demotion is correct.** Removing the BYOK key strips BYOK-only preference overrides (custom schedule, etc.) so a demoted user cannot continue using settings they're no longer paying for.

### Negative

- **API now accepts secrets.** This is a meaningful expansion of the API's threat surface. Mitigated by the table above, but anyone reviewing the system needs to understand we are deliberately crossing this line, not doing so by accident.
- **Validation costs one MarketCheck call.** The user's quota is debited one search at upsert time. The API uses a 1-mile-radius zip-only query, so this is essentially free relative to a real run.
- **Secret deletion is irreversible.** `ForceDeleteWithoutRecovery=True` skips the 7-day recovery window. If a user accidentally deletes their key, they must re-paste it. This is acceptable because (a) the user explicitly clicked "Remove", and (b) the recovery window otherwise blocks re-creation under the same name for 7 days.

## Alternatives considered

- **Cognito custom attribute.** Cognito user pools support custom attributes that could hold the key. Rejected because Cognito attributes are not encrypted at rest with our own KMS key, are returned in JWT claims by default (which would put the key in every API request), and have a 2 KB total limit shared across all custom attributes.
- **DynamoDB encrypted column.** Storing the encrypted key in the Users row. Rejected because DynamoDB's at-rest encryption uses an AWS-owned key by default; getting per-user envelope encryption right is more work than using Secrets Manager (which gets it right out of the box), and Secrets Manager has rotation support if we want it later.
- **Out-of-band only (status quo).** Continue admin-CLI provisioning. Rejected as a non-feature: BYOK without self-service is a marketing claim, not a product.
