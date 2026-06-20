# AWS Lambda Python 3.12 base, patched to clear all (fixable) CVEs.
# A Trivy diagnostic showed the only vulnerabilities were in libsolv (OS) and
# pip (build tooling) — both fixable; the application dependencies are clean.
# So no distroless/FAISS gymnastics are needed: we patch the base instead.
FROM public.ecr.aws/lambda/python:3.12

# Patch OS CVEs (libsolv, ...) and install the toolchain for native wheels (FAISS).
# `--releasever=latest` pulls the newest AL2023 repo snapshot so security fixes
# (e.g. the libsolv patch) land — the base image pins an older snapshot.
RUN dnf update -y --releasever=latest && dnf install -y gcc gcc-c++ make && dnf clean all

# Reproducible dependencies straight from uv.lock — no fresh resolution at build
# time — and upgrade pip itself (clears the pip CVEs Trivy flags on the base).
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req.txt \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/req.txt

COPY src/ src/
COPY evals/fixtures/ evals/fixtures/

# Lambda handler entrypoint (resolved from LAMBDA_TASK_ROOT).
CMD ["src.handler.handler"]
