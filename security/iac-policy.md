# Lookout IaC Security Baseline

Checkov scans the synthesized CloudFormation (`cdk synth` → `infrastructure/cdk.out`)
on the `dev → main` PR and the prod deploy. Checks in **the baseline**
(`security/checkov-baseline.txt`) are enforced with **zero tolerance** — any
violation blocks the merge/deploy, with no per-finding suppressions.

Checks **not** in the baseline are a **deliberate, reviewed scoping decision**
(documented below), *not* an ad-hoc skip. The baseline is intended to grow as the
project hardens toward production.

CI runs: `checkov -d infrastructure/cdk.out --framework cloudformation --check $(paste -sd, security/checkov-baseline.txt)`

## Enforced (baseline)

| Check | Control | Why enforced |
|-------|---------|--------------|
| **CKV_AWS_21** | S3 bucket versioning | Free and teardown-safe (`auto_delete_objects` handles dev DESTROY); guards the dashboard bucket. |

> The runs queue + DLQ are also encrypted at rest with **SSE-SQS** (free, AWS-owned key) — see the `CKV_AWS_27` note below for why that check itself is out of the enforced baseline.

## Out of baseline (deliberate policy)

| Check(s) | Control | Why deferred |
|----------|---------|--------------|
| CKV_AWS_119, 149, 158, 173 | KMS CMK on DynamoDB / Secrets Manager / CloudWatch Logs / Lambda env vars | Default AWS-managed encryption-at-rest is already on; a CMK adds key management + ongoing cost. Secrets live in Secrets Manager, not env vars, so env-var CMK is redundant. **Prod-hardening candidate.** |
| CKV_AWS_28 | DynamoDB point-in-time recovery | Small ongoing cost; a recommended **prod follow-up** (enable on prod tables). Dev tables are ephemeral (`RemovalPolicy.DESTROY`). |
| CKV_AWS_26 | SNS topic encryption | The alarm topic is published to by CloudWatch; SSE requires a customer-managed KMS key with a CloudWatch key-policy grant (cost + complexity) — the AWS-managed key would break alarm delivery. The topic carries only alarm-state metadata. |
| CKV_AWS_27 | SQS queue encryption via **KMS** | The queues already use **SSE-SQS** (`SqsManagedSseEnabled` — free, AWS-owned key; data IS encrypted at rest). CKV_AWS_27 requires SSE-**KMS** (`KmsMasterKeyId`) specifically: a customer CMK adds cost, and the AWS-managed `alias/aws/sqs` key risks the Lambda consumer's `kms:Decrypt` grant. Deferred — the data is encrypted regardless. |
| CKV_AWS_174 | CloudFront viewer TLS ≥ 1.2 | The distribution uses the default `*.cloudfront.net` certificate, whose minimum TLS is fixed by AWS; raising it requires a custom domain + ACM certificate. Deferred until a custom domain exists. |
| CKV_AWS_68, 86, 76, 120 | CloudFront WAF / CF access logs / API access logs / API caching | Prod uses WAF (dev uses an IP allowlist by design); access logging + caching are cost/ops choices deferred to prod. |
| CKV_AWS_73 | API Gateway X-Ray tracing | Intentionally off in dev (`enable_xray_tracing=False`); enabled in prod via config. |
| CKV_AWS_117, 116, 115 | Lambda in VPC / Lambda DLQ / reserved concurrency | The worker calls external APIs (no VPC/NAT by design); repeated failures are caught by the **SQS** DLQ + run records; concurrency is governed via pause/resume. |
| CKV_AWS_109 | IAM: no permissions-management without constraint | Flagged on broad **CDK-managed** custom-resource roles (BucketDeployment / LogRetention), not first-party policies. First-party IAM is already explicit-ARN scoped per the project's non-negotiable rules. |
