FROM public.ecr.aws/lambda/python:3.12

# System deps for FAISS native extensions. We dropped Playwright's chromium
# install — Lambda's runtime can't `apt-get` it, and we gate the trade-in
# scrapers off via DISABLE_TRADE_IN_SCRAPERS=true. Python `playwright` is
# still on the image (modules import it) but the browser binary is not.
RUN dnf install -y \
    gcc \
    gcc-c++ \
    make \
    && dnf clean all

# Copy source and install Python dependencies
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

COPY evals/fixtures/ evals/fixtures/

# Set the handler
CMD ["src.handler.handler"]
