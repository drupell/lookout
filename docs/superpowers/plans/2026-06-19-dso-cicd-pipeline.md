# DSO CI/CD Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a DevSecOps CI/CD pipeline on the public `drupell/lookout` repo — permissive on `dev`, zero-tolerance-strict on `prod` — with cross-account OIDC deploys, native enforcement, and resource tagging.

**Architecture:** Three GitHub Actions workflows (`ci`, `deploy-dev`, `deploy-prod`) gated by a `main` branch ruleset and a `production` Environment approval. Security scanners run warn-only on `dev` and blocking on PRs to `main`/prod. Two least-privilege OIDC roles (one per AWS account) let CI `cdk deploy` without long-lived keys; the prod role is assumable only from the approved `production` environment.

**Tech Stack:** GitHub Actions, AWS CDK (Python), OIDC federation, CodeQL, Trivy, Checkov, gitleaks, pip-audit/npm audit, Dependabot.

**Spec:** `docs/superpowers/specs/2026-06-19-dso-cicd-pipeline-design.md`

**User decisions (already made):**
- Prod security = "zero-tolerance, any finding from any scanner blocks, no suppressions, no escape hatch (incl. no-fix upstream CVEs)."
- dev security = warn-only; Checkov = "documented policy baseline" allowlist.
- Separate AWS accounts `lookout-dev` / `lookout-prod`; prod approval via `production` Environment required reviewer; PR approvals = 0.
- Default branch stays `main`; `dev` unprotected (auto-deploy on push). Tag resources Project/Environment.

**Shared naming (use verbatim across all tasks):**
- Roles: `lookout-gha-dev` (dev account), `lookout-gha-prod` (prod account)
- OIDC stacks: `LookoutDevOidc`, `LookoutProdOidc`
- Secrets: repo secret `AWS_DEV_DEPLOY_ROLE_ARN`; `production` env secret `AWS_PROD_DEPLOY_ROLE_ARN`
- Environment: `production` · Region: `us-east-1`
- Dev deploy scope: `LookoutDev LookoutDevMonitoring`; Prod: `LookoutProd LookoutProdMonitoring` (mirrors prior pipeline)

> **Note on TDD:** this plan is infrastructure/CI config, not application logic, so "tests" are `cdk synth`, `actionlint`, local scanner runs, and a real push observed end-to-end. Where a task touches Python, it gets a pytest. Tooling caveat: the local venv is broken — run Python tooling via `PY=$(uv python find 3.12); PYTHONPATH="$PWD:$PWD/.venv/lib/python3.12/site-packages" "$PY" -m <tool>` (see `~/.claude/.../memory/lookout-deploy-env.md`). Commit with `--no-verify` (the pre-commit hook wrapper is broken; run ruff manually).

---

### Task 0: Checkov IaC policy baseline

**Goal:** A documented, version-controlled IaC security standard (the explicit Checkov check-ID allowlist) that prod is held to with zero tolerance.

**Files:**
- Create: `security/iac-policy.md`
- Create: `security/checkov-baseline.txt` (machine-readable check-ID list, one per line)

**Acceptance Criteria:**
- [ ] `security/iac-policy.md` lists each in-scope Checkov check ID with a one-line rationale, and states out-of-scope = deliberate policy
- [ ] `security/checkov-baseline.txt` is a comma-free, newline-delimited list consumed by CI
- [ ] `checkov -d cdk.out --framework cloudformation --check $(paste -sd, security/checkov-baseline.txt)` runs and reports only baseline checks

**Verify:** `cd infrastructure && cdk synth >/dev/null && checkov -d cdk.out --framework cloudformation --check $(paste -sd, ../security/checkov-baseline.txt) --compact` → exits 0 (or lists only baseline failures to fix)

**Steps:**
- [ ] **Step 1: Generate the candidate findings to triage.** Run `cd infrastructure && cdk synth >/dev/null && checkov -d cdk.out --framework cloudformation --compact` and capture the full check-ID list.
- [ ] **Step 2: Author `security/iac-policy.md`** — for each check ID, decide in/out of baseline. Seed the baseline with the controls that match the project's non-negotiable rules (IAM least-privilege, encryption, no public exposure), e.g.:

```markdown
# Lookout IaC Security Baseline
Checks in this baseline are enforced with zero tolerance on the dev→main PR and prod deploy.
Checks NOT listed are a deliberate, reviewed scoping decision (e.g. dev cost-lean choices), not per-finding suppression.

| Check ID | Control | In baseline? | Rationale |
|----------|---------|--------------|-----------|
| CKV_AWS_18  | S3 access logging        | no  | dev buckets ephemeral; logging cost > value |
| CKV_AWS_21  | S3 versioning            | yes | protects deal/audit data |
| CKV_AWS_111 | IAM no write w/o constraint | yes | matches "explicit ARNs" rule |
| CKV_AWS_158 | CloudWatch log KMS encrypt | yes | audit logs at rest |
| ...         | ...                      | ... | ... |
```

- [ ] **Step 3: Write `security/checkov-baseline.txt`** — the `yes` check IDs, one per line.
- [ ] **Step 4: Verify** the Verify command above runs and only evaluates baseline checks. Iterate the baseline/infra until it passes (fixing real violations in the CDK, not skipping).
- [ ] **Step 5: Commit**

```bash
git add security/iac-policy.md security/checkov-baseline.txt
git commit --no-verify -m "feat(security): Checkov IaC policy baseline"
```

---

### Task 1: Minimize the Lambda base-image CVE surface

**Goal:** Replace the CVE-bearing Lambda base image so `trivy image` can pass under zero-tolerance.

**Files:**
- Modify: `Dockerfile`

**Acceptance Criteria:**
- [ ] Image builds for `linux/arm64` and the handler still loads (`src.handler.handler`)
- [ ] `trivy image --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL <tag>` reports **0** vulnerabilities (or only no-fix ones acknowledged as the accepted freeze risk per spec)
- [ ] FAISS + Python deps still import inside the container

**Verify:** `docker build --platform linux/arm64 -t lookout-test . && trivy image --exit-code 1 --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL lookout-test`

**Steps:**
- [ ] **Step 1: Switch to a minimal, low-CVE base with the Lambda Runtime Interface Client.** Primary approach — Chainguard's zero-CVE Python image + `awslambdaric`:

```dockerfile
# Build deps in a fuller image, run on a distroless/minimal one.
FROM cgr.dev/chainguard/python:latest-dev AS build
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --target /app/deps . awslambdaric

FROM cgr.dev/chainguard/python:latest
WORKDIR /app
COPY --from=build /app/deps /app/deps
COPY src/ src/
COPY evals/fixtures/ evals/fixtures/
ENV PYTHONPATH=/app/deps
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["src.handler.handler"]
```

- [ ] **Step 2: Build + smoke test locally** with the Verify command. If FAISS needs a system lib absent from the minimal base, add it in the build stage only.
- [ ] **Step 3: If Chainguard's Lambda contract proves fiddly, fall back** to `gcr.io/distroless/python3-debian12` + `awslambdaric` (same RIC pattern). Document whichever lands in a Dockerfile comment.
- [ ] **Step 4: Confirm 0 Trivy findings.** Any remaining finding must be a genuine no-fix upstream CVE (accepted freeze risk) — do NOT add `--ignore-unfixed`.
- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git commit --no-verify -m "feat(docker): minimal low-CVE Lambda base image (RIC)"
```

---

### Task 2: Refactor `GitHubOidcStack` → per-account/env roles + wire into the app

**Goal:** Two least-privilege OIDC deploy roles with corrected, exact-match trust; instantiated in `app.py` as `LookoutDevOidc` / `LookoutProdOidc`.

**Files:**
- Modify: `infrastructure/stacks/github_oidc_stack.py`
- Modify: `infrastructure/app.py`
- Test: `evals/test_oidc_stack.py` (new)

**Acceptance Criteria:**
- [ ] Dev role trusts `sub == repo:drupell/lookout:ref:refs/heads/dev` via **StringEquals** (no `:*` wildcard)
- [ ] Prod role trusts `sub == repo:drupell/lookout:environment:production` via **StringEquals**
- [ ] Each role's permission policy is `sts:AssumeRole` on exactly the four `cdk-hnb659fds-{deploy,file-publishing,image-publishing,lookup}-role-<acct>-us-east-1` ARNs — no wildcards, no extra perms
- [ ] `LookoutDevOidc` and `LookoutProdOidc` synthesize
- [ ] pytest asserts the prod template has no `StringLike` on `sub` and uses the environment sub

**Verify:** `cd infrastructure && cdk synth LookoutDevOidc LookoutProdOidc >/dev/null` and `PYTHONPATH=... "$PY" -m pytest evals/test_oidc_stack.py -v`

**Steps:**
- [ ] **Step 1: Write the failing test** `evals/test_oidc_stack.py`:

```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from infrastructure.stacks.github_oidc_stack import GitHubOidcStack

def _tmpl(env_name, sub):
    app = cdk.App()
    stack = GitHubOidcStack(app, f"Lookout{env_name}Oidc",
                            github_org="drupell", github_repo="lookout",
                            role_name=f"lookout-gha-{env_name.lower()}",
                            subject=sub, env=cdk.Environment(account="111111111111", region="us-east-1"))
    return Template.from_stack(stack)

def test_prod_uses_stringequals_environment_sub():
    t = _tmpl("Prod", "repo:drupell/lookout:environment:production")
    t.has_resource_properties("AWS::IAM::Role", Match.object_like({
        "AssumeRolePolicyDocument": {"Statement": [{"Condition": {"StringEquals": Match.object_like({
            "token.actions.githubusercontent.com:sub": "repo:drupell/lookout:environment:production"})}}]}}))

def test_no_wildcard_sub_anywhere():
    t = _tmpl("Dev", "repo:drupell/lookout:ref:refs/heads/dev")
    body = t.to_json()
    assert "StringLike" not in str(body), "OIDC trust must use StringEquals, not wildcard StringLike"
```

- [ ] **Step 2: Run it → FAIL** (`role_name`/`subject` kwargs don't exist yet).
- [ ] **Step 3: Refactor `github_oidc_stack.py`** to take `role_name` + `subject`, use `StringEquals`, and the four-ARN AssumeRole policy:

```python
self.deploy_role = iam.Role(
    self, "GitHubDeployRole", role_name=role_name,
    assumed_by=iam.WebIdentityPrincipal(
        oidc_provider.open_id_connect_provider_arn,
        conditions={"StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": subject,
        }},
    ),
    description=f"GitHub Actions deploy role ({subject})",
)
q = "cdk-hnb659fds"
self.deploy_role.add_to_policy(iam.PolicyStatement(
    effect=iam.Effect.ALLOW, actions=["sts:AssumeRole"],
    resources=[
        f"arn:aws:iam::{self.account}:role/{q}-deploy-role-{self.account}-{self.region}",
        f"arn:aws:iam::{self.account}:role/{q}-file-publishing-role-{self.account}-{self.region}",
        f"arn:aws:iam::{self.account}:role/{q}-image-publishing-role-{self.account}-{self.region}",
        f"arn:aws:iam::{self.account}:role/{q}-lookup-role-{self.account}-{self.region}",
    ],
))
```

- [ ] **Step 4: Wire into `app.py`** (after `app = cdk.App()`), one stack per account env:

```python
from infrastructure.stacks.github_oidc_stack import GitHubOidcStack
GitHubOidcStack(app, "LookoutDevOidc", github_org="drupell", github_repo="lookout",
                role_name="lookout-gha-dev",
                subject="repo:drupell/lookout:ref:refs/heads/dev",
                env=US_EAST_1, description="GitHub OIDC deploy role — dev account")
GitHubOidcStack(app, "LookoutProdOidc", github_org="drupell", github_repo="lookout",
                role_name="lookout-gha-prod",
                subject="repo:drupell/lookout:environment:production",
                env=US_EAST_1, description="GitHub OIDC deploy role — prod account")
```

- [ ] **Step 5: Run test → PASS**, `cdk synth LookoutDevOidc LookoutProdOidc` succeeds.
- [ ] **Step 6: Commit**

```bash
git add infrastructure/stacks/github_oidc_stack.py infrastructure/app.py evals/test_oidc_stack.py
git commit --no-verify -m "feat(infra): per-account OIDC deploy roles, exact-match env-scoped trust"
```

---

### Task 3: CDK resource tagging (Project / Environment / ManagedBy)

**Goal:** Every CDK-managed resource carries `Project`, `Environment`, `ManagedBy` tags.

**Files:**
- Modify: `infrastructure/app.py`
- Test: `evals/test_tagging.py` (new)

**Acceptance Criteria:**
- [ ] Synthesized dev resources carry `Project=Lookout`, `Environment=dev`, `ManagedBy=cdk`; prod carry `Environment=prod`
- [ ] Applied per-stack-group so dev/prod get the right `Environment` value

**Verify:** `PYTHONPATH=... "$PY" -m pytest evals/test_tagging.py -v`

**Steps:**
- [ ] **Step 1: Failing test** `evals/test_tagging.py` asserting a known resource (e.g. a DynamoDB table) in `LookoutDev` has Tag `Environment=dev` and `Project=Lookout`.
- [ ] **Step 2: Apply tags** — tag each environment's stacks via their config. Simplest: after each stack group, `cdk.Tags.of(<stack>).add(...)`, or apply app-wide Project/ManagedBy + per-group Environment:

```python
cdk.Tags.of(app).add("Project", "Lookout")
cdk.Tags.of(app).add("ManagedBy", "cdk")
for s in (dev_dashboard, dev_auth, dev_agent, dev_api, dev_monitoring):
    cdk.Tags.of(s).add("Environment", "dev")
for s in (prod_dashboard, prod_auth, prod_agent, prod_api, prod_monitoring):
    cdk.Tags.of(s).add("Environment", "prod")
```

- [ ] **Step 3: Run test → PASS.**
- [ ] **Step 4: Commit**

```bash
git add infrastructure/app.py evals/test_tagging.py
git commit --no-verify -m "feat(infra): tag all resources Project/Environment/ManagedBy"
```

---

### Task 4: [USER-RUN, AWS admin] Bootstrap accounts + deploy OIDC stacks

**Goal:** OIDC providers + deploy roles exist in both accounts; bootstrap is in place. (Deploy is a hard gate — the user runs these with admin creds for each account; the agent surfaces exact commands and does not run them.)

**Files:** none (operational)

**Acceptance Criteria:**
- [ ] dev + prod accounts bootstrapped (CDKToolkit stack present), with a scoped CFN-exec policy for prod (not `AdministratorAccess`)
- [ ] `LookoutDevOidc` deployed to dev account, `LookoutProdOidc` to prod account
- [ ] Both role ARNs captured for Task 5

**Verify:** `aws iam get-role --role-name lookout-gha-dev` (dev creds) and `aws iam get-role --role-name lookout-gha-prod` (prod creds) return the roles.

**Steps (surface to user; they run):**
- [ ] **dev account** (admin creds for `lookout-dev`):

```bash
cd infrastructure
cdk bootstrap aws://<DEV_ACCT>/us-east-1 \
  --cloudformation-execution-policies arn:aws:iam::aws:policy/AdministratorAccess
cdk deploy LookoutDevOidc --require-approval never
# capture: aws cloudformation describe-stacks --stack-name LookoutDevOidc \
#   --query "Stacks[0].Outputs" --output table   (DeployRoleArn)
```

- [ ] **prod account** (admin creds for `lookout-prod`) — scope the CFN-exec policy:

```bash
cdk bootstrap aws://<PROD_ACCT>/us-east-1 \
  --cloudformation-execution-policies arn:aws:iam::<PROD_ACCT>:policy/<lookout-prod-deploy-policy>
cdk deploy LookoutProdOidc --require-approval never
```

- [ ] **Step 3:** Record both `DeployRoleArn` outputs for Task 5.

---

### Task 5: Configure GitHub — secrets, `production` environment, `main` ruleset, security settings

**Goal:** All native enforcement + secrets in place via `gh`.

**Files:** none (GitHub config; agent runs `gh`)

**Acceptance Criteria:**
- [ ] Repo secret `AWS_DEV_DEPLOY_ROLE_ARN` set; `production` env secret `AWS_PROD_DEPLOY_ROLE_ARN` set
- [ ] `production` Environment exists with a required reviewer (self-review left ON — solo caveat per spec)
- [ ] `main` ruleset: PR required, no direct push, include admins, required checks (CI jobs + no-open-PR), "require code scanning results" (CodeQL, threshold `all`)
- [ ] Secret scanning + push protection + Dependabot enabled

**Verify:** `gh api repos/drupell/lookout/environments/production` and `gh api repos/drupell/lookout/rulesets` return the configured objects.

**Steps:**
- [ ] **Step 1: Secrets** (after Task 4 ARNs exist):

```bash
gh secret set AWS_DEV_DEPLOY_ROLE_ARN  -b "arn:aws:iam::<DEV_ACCT>:role/lookout-gha-dev"
gh api -X PUT repos/drupell/lookout/environments/production   # create env
gh secret set AWS_PROD_DEPLOY_ROLE_ARN --env production -b "arn:aws:iam::<PROD_ACCT>:role/lookout-gha-prod"
```

- [ ] **Step 2: Required reviewer on production** (replace `<USER_ID>` = your numeric id from `gh api user -q .id`):

```bash
gh api -X PUT repos/drupell/lookout/environments/production -f 'reviewers[][type=User]' -F 'reviewers[][id]=<USER_ID>'
```

- [ ] **Step 3: main ruleset** — `gh api -X POST repos/drupell/lookout/rulesets --input ruleset.json` where `ruleset.json` requires `pull_request`, `non_fast_forward`, required status checks (`ci / strict`, `no-open-prs`), `code_scanning` (CodeQL `security_alerts_threshold: all`, `alerts_threshold: all`), targeting `refs/heads/main`, `enforcement: active`, and bypass actors empty (include admins).
- [ ] **Step 4: Security settings:**

```bash
gh api -X PATCH repos/drupell/lookout -F security_and_analysis='{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}'
# Dependabot alerts:
gh api -X PUT repos/drupell/lookout/vulnerability-alerts
```

- [ ] **Step 5:** Verify with the Verify commands.

---

### Task 6: `ci.yml` — lint/type/evals/synth + zero-tolerance security suite + no-open-PR gate

**Goal:** The validation workflow: full quality + security, warn-only on dev, blocking on PRs to `main`.

**Files:**
- Create: `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [ ] Runs on push + PR; jobs: `quality` (ruff, mypy, pytest evals, cdk synth), `security` (gitleaks, pip-audit, npm audit, Trivy image+fs, Checkov baseline), `no-open-prs` (PRs to main only)
- [ ] `security` is soft-fail on `dev` pushes/PRs, hard-fail on PRs targeting `main`
- [ ] `actionlint .github/workflows/ci.yml` passes

**Verify:** `actionlint .github/workflows/ci.yml` → no errors; open a draft PR to confirm jobs run.

**Steps:**
- [ ] **Step 1: Author `ci.yml`** (complete):

```yaml
name: ci
on:
  push: { branches: [dev] }
  pull_request: { branches: [dev, main] }
permissions: { contents: read, security-events: write, pull-requests: read }
env:
  STRICT: ${{ github.base_ref == 'main' }}   # true on PRs into main
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: pip install -e ".[dev]"
      - run: ruff check src/ evals/ infrastructure/ scripts/
      - run: ruff format --check src/ evals/ infrastructure/ scripts/
      - run: mypy src/ || echo "mypy non-blocking"
      - run: python -m pytest evals/ -v --tb=short
      - run: npm install -g aws-cdk
      - run: cd infrastructure && cdk synth >/dev/null
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: gitleaks (full history)
        run: |
          curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_8.21.2_linux_x64.tar.gz | tar -xz gitleaks
          ./gitleaks git . --exit-code $([ "$STRICT" = true ] && echo 1 || echo 0) --redact -v
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]" pip-audit checkov
      - name: pip-audit
        run: pip-audit || [ "$STRICT" != true ]
      - name: npm audit (frontend)
        run: cd frontend && npm ci && (npm audit --audit-level=low || [ "$STRICT" != true ])
      - name: Trivy image + fs
        run: |
          docker build --platform linux/arm64 -t lookout:${{ github.sha }} .
          EC=$([ "$STRICT" = true ] && echo 1 || echo 0)
          trivy image --exit-code $EC --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL lookout:${{ github.sha }}
          trivy fs    --exit-code $EC --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL .
      - name: Checkov (policy baseline)
        run: |
          npm install -g aws-cdk && cd infrastructure && cdk synth >/dev/null && cd ..
          checkov -d infrastructure/cdk.out --framework cloudformation \
            --check $(paste -sd, security/checkov-baseline.txt) \
            $([ "$STRICT" = true ] && echo "" || echo "--soft-fail") --compact
  no-open-prs:
    if: github.base_ref == 'main'
    runs-on: ubuntu-latest
    permissions: { pull-requests: read }
    steps:
      - name: Fail if any other PR is open
        env: { GH_TOKEN: "${{ github.token }}" }
        run: |
          others=$(gh pr list --repo ${{ github.repository }} --state open --json number \
            --jq "[.[] | select(.number != ${{ github.event.pull_request.number }})] | length")
          test "$others" -eq 0 || { echo "::error::$others other open PR(s) — close before dev→main"; exit 1; }
```

- [ ] **Step 2: `actionlint`** the file; fix any lint. Pin the gitleaks version to a current release.
- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit --no-verify -m "ci: quality + zero-tolerance security suite + no-open-PR gate"
```

---

### Task 7: `deploy-dev.yml` — auto-deploy dev on push

**Goal:** On push to `dev`, after CI, assume the dev role and `cdk deploy` the dev stacks.

**Files:**
- Create: `.github/workflows/deploy-dev.yml`

**Acceptance Criteria:**
- [ ] Triggers on push to `dev` only; `needs`-gates on CI success (via `workflow_run` or a combined job)
- [ ] Assumes `AWS_DEV_DEPLOY_ROLE_ARN` via OIDC; `cdk deploy LookoutDev LookoutDevMonitoring --require-approval never`
- [ ] `actionlint` passes

**Verify:** `actionlint .github/workflows/deploy-dev.yml`; after merge, a push to dev deploys (observe the run).

**Steps:**
- [ ] **Step 1: Author** (uses `workflow_run` to chain after `ci`):

```yaml
name: deploy-dev
on:
  workflow_run: { workflows: [ci], types: [completed], branches: [dev] }
permissions: { id-token: write, contents: read }
jobs:
  deploy:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    concurrency: deploy-dev
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: pip install -e ".[dev]" && npm install -g aws-cdk
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEV_DEPLOY_ROLE_ARN }}
          aws-region: us-east-1
      - run: cd infrastructure && cdk deploy LookoutDev LookoutDevMonitoring --require-approval never
```

- [ ] **Step 2: `actionlint`; commit**

```bash
git add .github/workflows/deploy-dev.yml
git commit --no-verify -m "ci: auto-deploy dev on push (OIDC, dev account)"
```

---

### Task 8: `deploy-prod.yml` — environment-gated prod deploy

**Goal:** On push to `main`, run strict checks then pause at the `production` environment for approval before deploying to the prod account.

**Files:**
- Create: `.github/workflows/deploy-prod.yml`

**Acceptance Criteria:**
- [ ] Triggers on push to `main`; the deploy job declares `environment: production` (the approval pause + the only context where the prod role's `sub` matches)
- [ ] Assumes `AWS_PROD_DEPLOY_ROLE_ARN`; `cdk deploy LookoutProd LookoutProdMonitoring`
- [ ] `concurrency: deploy-prod` prevents overlap; `actionlint` passes

**Verify:** `actionlint .github/workflows/deploy-prod.yml`; after a `dev→main` merge, the job shows "Waiting" until approved.

**Steps:**
- [ ] **Step 1: Author** (re-runs strict gates, then gated deploy):

```yaml
name: deploy-prod
on:
  push: { branches: [main] }
permissions: { id-token: write, contents: read, security-events: write }
jobs:
  strict-gate:
    uses: ./.github/workflows/ci.yml      # reuse CI as the strict re-check
  deploy:
    needs: strict-gate
    runs-on: ubuntu-latest
    environment: production               # <- approval pause + prod-role sub match
    concurrency: deploy-prod
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: pip install -e ".[dev]" && npm install -g aws-cdk
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_PROD_DEPLOY_ROLE_ARN }}
          aws-region: us-east-1
      - run: cd infrastructure && cdk diff LookoutProd LookoutProdMonitoring || true
      - run: cd infrastructure && cdk deploy LookoutProd LookoutProdMonitoring --require-approval never
```

> Note: making `ci.yml` reusable via `workflow_call` (add `on: workflow_call:`) is required for the `uses:` above; include that trigger when authoring Task 6.

- [ ] **Step 2: `actionlint`; commit**

```bash
git add .github/workflows/deploy-prod.yml
git commit --no-verify -m "ci: environment-gated prod deploy (OIDC, prod account)"
```

---

### Task 9: Dependabot + CodeQL enablement

**Goal:** Automated dependency bumps + CodeQL code scanning feeding the merge-protection rule.

**Files:**
- Create: `.github/dependabot.yml`
- Create: `.github/workflows/codeql.yml` (advanced setup, py + js-ts, security-extended)

**Acceptance Criteria:**
- [ ] Dependabot configured for `pip` (root), `npm` (frontend), and `github-actions`
- [ ] CodeQL workflow scans Python + JavaScript/TypeScript with `security-extended`, on push/PR + schedule
- [ ] CodeQL results appear in the Security tab (feeds the Task 5 ruleset)

**Verify:** `actionlint .github/workflows/codeql.yml`; after merge, the CodeQL run uploads results.

**Steps:**
- [ ] **Step 1: `.github/dependabot.yml`:**

```yaml
version: 2
updates:
  - { package-ecosystem: pip, directory: "/", schedule: { interval: weekly } }
  - { package-ecosystem: npm, directory: "/frontend", schedule: { interval: weekly } }
  - { package-ecosystem: github-actions, directory: "/", schedule: { interval: weekly } }
```

- [ ] **Step 2: `.github/workflows/codeql.yml`** using `github/codeql-action` (init with `queries: security-extended`, `languages: python, javascript-typescript`; analyze).
- [ ] **Step 3: `actionlint`; commit**

```bash
git add .github/dependabot.yml .github/workflows/codeql.yml
git commit --no-verify -m "ci: Dependabot + CodeQL (security-extended)"
```

---

### Task 10: End-to-end verification

**Goal:** Prove the pipeline behaves per the acceptance criteria, on the real repo.

**Files:** none (operational)

**Acceptance Criteria:**
- [ ] Push a trivial change to `dev` → CI passes (security warn-only) → `LookoutDev` auto-deploys
- [ ] Open a `dev→main` PR with a deliberately-vulnerable dep → strict security **blocks** the PR; revert
- [ ] Open a second PR → the `no-open-prs` gate blocks the `dev→main` PR
- [ ] Direct `git push origin main` is rejected by the ruleset (even as admin)
- [ ] Merge a clean `dev→main` PR → `deploy-prod` shows "Waiting" at `production`; approve → `LookoutProd` deploys via the prod role
- [ ] Confirm a non-environment job cannot assume `lookout-gha-prod` (negative test)

**Verify:** observe each run in the Actions tab + `aws sts get-caller-identity` in the prod job logs shows the prod account.

**Steps:**
- [ ] Run each scenario above; capture run URLs. Fix any gate that doesn't fire as specified. Update the spec if reality diverges.

---

## Self-Review

- **Spec coverage:** §1 flow → Tasks 6/7/8; §2 security → Tasks 0/1/6/9; §3 OIDC roles → Tasks 2/4; §4 enforcement → Tasks 3/5; prerequisites → Tasks 0/1/2/4/5; acceptance criteria → Task 10. ✓ No gaps.
- **Naming consistency:** roles `lookout-gha-{dev,prod}`, secrets `AWS_{DEV,PROD}_DEPLOY_ROLE_ARN`, env `production`, stacks `Lookout{Dev,Prod}Oidc`, deploy scope `Lookout{Dev,Prod} Lookout{Dev,Prod}Monitoring` — used identically across Tasks 2/4/5/7/8. ✓
- **Cross-task dependency:** Task 8's reusable-CI `uses:` requires Task 6 to add `on: workflow_call:` — noted inline in Task 8. ✓
- **No placeholders:** AWS account IDs (`<DEV_ACCT>`/`<PROD_ACCT>`) and `<USER_ID>` are genuine runtime values the user supplies, not design gaps. ✓
