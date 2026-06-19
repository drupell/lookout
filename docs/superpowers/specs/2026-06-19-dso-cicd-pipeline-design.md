# DSO CI/CD Pipeline — Design Spec

- **Status:** Approved (design); ready for implementation planning
- **Date:** 2026-06-19
- **Repo:** `github.com/drupell/lookout` (public). Backup: private `drupell/lookout-archive`.
- **Author:** Dave Rupell

## Context

The new public repo has **no CI** (the old `deploy.yml` was dropped — it was non-functional: it died at `configure-aws-credentials` because `AWS_DEPLOY_ROLE_ARN` was never set, so nothing ever deployed). We are building a DevSecOps pipeline from a clean slate. Going public unlocked the native primitives this needs — branch protection / rulesets, GitHub Environments with required reviewers, and free CodeQL / secret scanning / Dependabot — none of which are free on a private repo.

## Goals

1. **Pipeline-gated deploys** — stop relying on manual `cdk deploy` as the path to AWS.
2. **DevSecOps**: security scanning in the pipeline — **permissive on `dev`**, **extremely strict on `prod`**.
3. **Promotion discipline**: `prod` reachable only via a `dev → main` MR + approval; no direct commits to `main`; no other open PRs at merge time.
4. **Least privilege, no long-lived creds** (per project non-negotiable rules): OIDC federation, explicit-ARN IAM, separate dev/prod identities.
5. **Tag every deployed resource** with project + environment.

## Non-Goals

- Multi-region. Single region per account (current).
- Org-level rulesets / GitHub Advanced Security (require paid plans; repo-level rulesets on a public repo suffice).
- Replacing the existing CDK stacks' design — this is the delivery pipeline around them.

## Constraints (locked decisions)

| Decision | Choice |
|---|---|
| Prod security threshold | **Zero-tolerance** — any finding, any scanner, blocks. **No suppressions, no escape hatch** (incl. no-fix upstream CVEs). |
| dev security | Warn-only (annotations / SARIF upload), never blocks. |
| Checkov scope | **Documented policy baseline** — an explicit, version-controlled allowlist of check-IDs (`security/iac-policy.md`); zero violations of the baseline tolerated. |
| AWS accounts | **Separate** `lookout-dev` and `lookout-prod` accounts. |
| Prod approval | GitHub Environment `production` required-reviewer gate (PR approvals = 0). |
| Default branch | `main` (scaffold until first release). |
| `dev` branch | Unprotected (direct push → CI → auto-deploy dev). |
| Required PR approvals | 0 (solo dev can't self-approve a PR; approval lives at the deploy gate). |

---

## Section 1 — Promotion & deploy flow

Three workflow files, separated by concern (the old single `deploy.yml` mixed everything and was unreadable):

- **`ci.yml`** — runs on every push and PR. Lint · typecheck · 416 evals · `cdk synth` · security suite. **Security is warn-only for `dev` pushes/PRs; blocking for PRs targeting `main`.**
- **`deploy-dev.yml`** — on push to `dev`, after `ci.yml` passes → assume the dev-account role → `cdk deploy` LookoutDev. **Auto** (fast iteration).
- **`deploy-prod.yml`** — on push to `main` (i.e. a merged `dev → main` PR) → re-run strict CI → **`production` Environment gate (required reviewer = you) pauses the job** → on approval, assume the prod-account role → `cdk deploy` LookoutProd.

```
push dev ─► ci.yml (permissive) ──pass──► deploy-dev.yml ─► cdk deploy LookoutDev   [auto]

PR dev►main ─► ci.yml (STRICT, security BLOCKING) + "no other open PRs" gate
            └─ enforced as a branch RULESET on main (PR required, no direct push,
               checks must pass, include admins, 0 approvals)

merge main ─► deploy-prod.yml ─► [production env: required-reviewer pause] ─► approve
            └─► assume prod role (only assumable from this env) ─► cdk deploy LookoutProd
```

**Why approval lives at the deploy gate, not the PR:** a solo maintainer cannot approve their own PR, so required PR reviews would deadlock — hence PR approvals = 0, and the human gate is the `production` Environment's required-reviewer pause on the *deploy job*.

---

## Section 2 — DSO security suite

| Tool | Scans | "A finding" (prod = block, dev = warn) | Notes |
|---|---|---|---|
| **CodeQL** | Python + TS | `security-extended` suite; block via ruleset, threshold `all` | free on public; results in Security tab |
| **gitleaks** | tree + **full git history** | any secret → block (incl. dev) | `gitleaks git` (not deprecated `detect`); `fetch-depth: 0`; **no license needed (personal account)** |
| **pip-audit / npm audit** | lockfiles | any advisory → block | `npm audit --audit-level=low`; pip-audit exits 1 on any |
| **Trivy** | **`trivy image`** (OS+lang) + `trivy fs` (deps/IaC/secrets) | all severities `UNKNOWN→CRITICAL`, `--exit-code 1` | `--ignore-unfixed` deliberately **OFF**; distroless base image shrinks surface |
| **Checkov** | `cdk synth` CFN output | violations of the documented baseline allowlist | `-d cdk.out --framework cloudformation --check <IDs>` |
| **Dependabot** + native secret scanning + push protection | — | auto-bump PRs; block secret pushes at commit time | free on public |

**Zero-tolerance mechanics:** every scanner is configured to exit non-zero on its first in-scope finding. On `dev` the security job runs with `continue-on-error` / soft-fail (uploads SARIF, annotates, never fails the run). On the `dev → main` PR and `prod`, the same job is hard-fail and is a required check.

**Checkov baseline:** `--check` is an *allowlist* (runs only the named IDs). We deliberately scope to a documented standard (`security/iac-policy.md`); within that standard, zero violations. This is a policy decision, not a per-finding suppression.

---

## Section 3 — AWS auth & least-privilege deploy roles

Two OIDC-federated roles, **one per account**, refactoring `infrastructure/stacks/github_oidc_stack.py` to be per-account/env. Direct topology (each account's role assumes *its own* `cdk-hnb659fds-*` bootstrap roles — no cross-account `--trust`).

- **`lookout-gha-dev`** (dev account) — trust `sub` pinned to `repo:drupell/lookout:ref:refs/heads/dev`.
- **`lookout-gha-prod`** (prod account) — trust `sub` pinned with **`StringEquals`** to **`repo:drupell/lookout:environment:production`**. Because the `environment:` segment only appears in the OIDC token when a job declares `environment: production`, this role is **un-assumable from any PR, any branch, or any non-prod job** — the IAM trust boundary and the human approval gate are the same boundary.

> ⚠️ **Correction from the current code:** `GitHubOidcStack` today uses `StringLike` with `repo:{org}/{repo}:*`. That wildcard matches *any* ref/PR/environment and would let a dev push assume the prod role. The prod role **must** use `StringEquals` on the exact environment `sub`.

**Role permissions (minimal):** each GHA role needs only `sts:AssumeRole` on the four `cdk-hnb659fds-{deploy,file-publishing,image-publishing,lookup}-role-<acct>-<region>` ARNs (explicit ARNs, no wildcard). It does **not** need CloudFormation/S3/ECR perms — the bootstrap roles hold those.

**Blast radius = the CFN execution role:** `cdk-hnb659fds-cfn-exec-role` is the identity that actually creates resources. Prod's must be **scoped** (not the default `AdministratorAccess`) per the no-wildcard rule; this is the real least-privilege lever.

**Secrets:** `lookout-gha-dev` ARN = repo secret; `lookout-gha-prod` ARN = **`production` Environment secret** (only the gated prod job can read it). Both deploy jobs set `permissions: id-token: write`; audience pinned to `sts.amazonaws.com`. (CI itself uses `cdk synth`, which needs **no** AWS credentials — only the two deploy jobs assume roles, so the dev role stays narrowly pinned to `ref:refs/heads/dev` and never needs to trust `:pull_request`.)

> Caveat (documented): GitHub's **immutable subject claims** take effect 2026-07-15. Our repo was created 2026-06-19, so it keeps the legacy `sub` format. If we ever opt in (or for any repo created after that date), the `sub` embeds owner/repo IDs (`repo:owner@ID/repo@ID:environment:production`) and the trust policy string must be updated. Verify the real `sub` before pinning.

---

## Section 4 — Enforcement & repo config

**Branch ruleset on `main`** (repo-level, free on public):
- Require a PR; **no direct pushes**.
- Required checks: strict `ci.yml` jobs + the security gate + the no-open-PR gate.
- **"Require code scanning results"** rule (CodeQL), thresholds = `all` — this is the CodeQL block, *not* a status check.
- 0 required approvals; **include administrators** (rule binds even repo admins); no force-push; require up-to-date.

**`dev`:** unprotected — CI runs on every push; "never break dev" is served by CI, not by blocking pushes.

**`production` Environment:** required reviewer = you. Holds the prod role ARN. *Solo-dev caveat (documented):* "Prevent self-review" is left **OFF** — with a single maintainer, enabling it would deadlock all deploys. So today the gate is a **deliberate-action** gate (you click "Approve and deploy"), not two-person segregation of duties. Add `concurrency:` to `deploy-prod.yml` to prevent overlapping prod deploys (the gate is per-job, not a release lock).

**"No other open PRs" gate:** a CI step on the `dev → main` PR runs `gh pr list --state open` and fails if any PR *other than this one* is open. Required check.

**Resource tagging:** in `infrastructure/app.py`, `Tags.of(app).add("Project", "Lookout")`, `add("Environment", <dev|prod>)`, `add("ManagedBy", "cdk")` → every CDK-managed resource tagged.

**Repo settings to enable:** Dependabot, CodeQL (default or advanced setup), secret scanning + push protection, auto-delete merged head branches.

---

## Accepted trade-offs & risks

1. **Prod can freeze on upstream CVEs.** Zero-tolerance + no suppressions means a base-image or transitive-dep CVE with no upstream fix blocks *all* prod deploys until it's fixed — including unrelated hotfixes. **Accepted.** Mitigations: distroless/minimal base image, pinned + audited deps, Dependabot auto-bumps.
2. **Prod approval is not true segregation of duties** while solo (self-review can't be prevented without deadlocking). **Accepted and documented;** revisit if a second maintainer joins.
3. **A misconfigured/removed CodeQL setup blocks merges** (ruleset fails closed when the tool isn't reporting). This is the safe direction; just operationally known.

## Acceptance criteria

- [ ] Push to `dev` → CI passes (permissive security) → `LookoutDev` auto-deploys via `lookout-gha-dev`.
- [ ] Open `dev → main` PR → strict security suite runs and **blocks** on any finding; "no other open PRs" gate blocks if another PR is open; CodeQL ruleset blocks on alerts.
- [ ] Direct push to `main` is rejected (ruleset); admins included.
- [ ] Merge to `main` → `deploy-prod.yml` pauses at `production` for approval → on approval, `LookoutProd` deploys via `lookout-gha-prod`.
- [ ] `lookout-gha-prod` is **not** assumable from a dev/PR job (verify: a non-environment job fails `AssumeRoleWithWebIdentity`).
- [ ] Every deployed resource carries `Project`, `Environment`, `ManagedBy` tags.
- [ ] No long-lived AWS keys anywhere; all IAM statements name explicit ARNs.

## Implementation prerequisites & sequence

1. **Minimize the Lambda base-image CVE surface** (`Dockerfile`) — the current AWS Lambda Python base image carries OS CVEs; evaluate a minimal/distroless base (+ the Lambda Runtime Interface Client) or a Chainguard-style image. Non-trivial (must keep the Lambda runtime contract working) — do this *before* turning on zero-tolerance, or prod is blocked on day one.
2. **Define `security/iac-policy.md`** — the Checkov baseline check-ID allowlist.
3. **Refactor `github_oidc_stack.py`** → per-account/env roles with corrected `StringEquals` trust; scope the prod CFN-exec policy.
4. **(User / AWS admin)** `cdk bootstrap` both accounts; deploy the OIDC stack to each; capture the two role ARNs. *(Deploy is a hard gate — surfaced commands, user runs them.)*
5. **Set secrets:** dev role ARN (repo secret), prod role ARN (`production` env secret).
6. **Create** the `production` Environment (+ reviewer), the `main` ruleset, and enable repo security settings — via `gh api` where possible.
7. **Author** `ci.yml`, `deploy-dev.yml`, `deploy-prod.yml`, `.github/dependabot.yml`, CodeQL setup.
8. **Add CDK tagging** in `app.py`.
9. **Verify** end-to-end against the acceptance criteria.

## Appendix — verified config (sources: GitHub & AWS official docs, 2026)

**Prod role trust (exact pin):**
```json
"Condition": { "StringEquals": {
  "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
  "token.actions.githubusercontent.com:sub": "repo:drupell/lookout:environment:production"
}}
```

**CodeQL blocking ruleset (`POST /repos/drupell/lookout/rulesets`):** `type: code_scanning`, `security_alerts_threshold: all`, `alerts_threshold: all`, targeting `refs/heads/main`, `enforcement: active`.

**Scanner invocations:**
```bash
trivy image --exit-code 1 --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL <image>
trivy fs    --exit-code 1 --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL .
checkov -d cdk.out --framework cloudformation --check <baseline-ids>   # allowlist; hard-fail default
gitleaks git . --exit-code 1 --redact -v        # checkout fetch-depth: 0; no GITLEAKS_LICENSE (personal acct)
pip-audit                                        # exit 1 on any vuln
npm audit --audit-level=low                      # 'moderate' not 'medium'; low for zero-tolerance
```

**GHA role permission policy (minimal):** `sts:AssumeRole` on the four `cdk-hnb659fds-{deploy,file-publishing,image-publishing,lookup}-role-<acct>-<region>` ARNs only.
