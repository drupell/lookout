.PHONY: dev install eval lint format typecheck test check pause resume synth diff-dev diff-prod deploy-dev deploy-prod dashboard install-dashboard wipe-dev-deals invoke-dev fresh-run frontend-install frontend-dev frontend-lint frontend-format frontend-typecheck frontend-test frontend-check frontend-build frontend-deploy-dev frontend-deploy-prod diff-dashboard-dev diff-dashboard-prod deploy-dashboard-dev deploy-dashboard-prod

install:
	uv sync --extra dev

dev: install
	uv run playwright install chromium

eval:
	uv run pytest evals/ -v --tb=short

lint:
	uv run ruff check src/ evals/ infrastructure/ scripts/
	uv run ruff format --check src/ evals/ infrastructure/ scripts/

format:
	uv run ruff check --fix src/ evals/ infrastructure/ scripts/
	uv run ruff format src/ evals/ infrastructure/ scripts/

typecheck:
	uv run mypy src/

test: lint eval

# Full quality gate — run before pushing. CI runs the same.
check: lint typecheck eval

pause:
	bash scripts/pause.sh

resume:
	bash scripts/resume.sh

synth:
	cd infrastructure && cdk synth

diff-dev:
	cd infrastructure && cdk diff LookoutDev

diff-prod:
	cd infrastructure && cdk diff LookoutProd

deploy-dev:
	cd infrastructure && cdk deploy LookoutDev --require-approval never

deploy-prod:
	cd infrastructure && cdk deploy LookoutProd --require-approval never

install-dashboard:
	uv sync --extra dashboard

dashboard:
	PYTHONPATH=. uv run streamlit run src/dashboard/app.py

# --- Next.js dashboard (frontend/) ---

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-lint:
	cd frontend && npm run lint

frontend-format:
	cd frontend && npm run format

frontend-typecheck:
	cd frontend && npm run typecheck

frontend-test:
	cd frontend && npm run test

frontend-check:
	cd frontend && npm run check

frontend-build:
	cd frontend && npm run build

# Build with stack-derived env vars + sync to S3 + invalidate CloudFront.
# Run AFTER `make deploy-dashboard-{dev,prod}` so the bucket + distribution exist.
frontend-deploy-dev:
	bash scripts/deploy_frontend.sh dev

frontend-deploy-prod:
	bash scripts/deploy_frontend.sh prod

# CloudFront/S3 infra only — content is pushed by frontend-deploy-*.
diff-dashboard-dev:
	cd infrastructure && cdk diff LookoutDevDashboard

diff-dashboard-prod:
	cd infrastructure && cdk diff LookoutProdDashboard

deploy-dashboard-dev:
	cd infrastructure && cdk deploy LookoutDevDashboard --require-approval never

deploy-dashboard-prod:
	cd infrastructure && cdk deploy LookoutProdDashboard --require-approval never

# AWS CLI profile for the dev-iteration targets below.
# Override per-invocation: `AWS_PROFILE=my-profile make invoke-dev`.
AWS_PROFILE ?= lookout-dev-admin

# --- Dev-iteration shortcuts ---

wipe-dev-deals:
	@echo "Wiping all rows from lookout-dev-deals..."
	@aws dynamodb scan --profile $(AWS_PROFILE) --region us-east-1 \
	    --table-name lookout-dev-deals \
	    --query 'Items[*].{listing_id:listing_id,first_seen:first_seen}' --output json \
	  | jq -c '.[] | {listing_id: {S: .listing_id.S}, first_seen: {S: .first_seen.S}}' \
	  | while read -r key; do \
	      aws dynamodb delete-item --profile $(AWS_PROFILE) --region us-east-1 \
	        --table-name lookout-dev-deals --key "$$key" >/dev/null; \
	    done
	@echo "Done."

invoke-dev:
	@aws lambda invoke --profile $(AWS_PROFILE) --region us-east-1 \
	    --cli-read-timeout 360 --cli-connect-timeout 30 \
	    --function-name lookout-dev \
	    --payload file://evals/fixtures/test_schedule_event.json \
	    --cli-binary-format raw-in-base64-out \
	    --log-type Tail \
	    /tmp/lookout-response.json >/dev/null
	@cat /tmp/lookout-response.json && echo

fresh-run: invoke-dev   # Lambda wipes the deals table itself when REFRESH_DEALS_ON_RUN=true
