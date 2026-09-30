# syntax=docker/dockerfile:1.7
# Control plane image (API and worker). Build context: the repository root.

# Node agent binaries, uploaded to each cluster's private artifacts bucket in real mode.
FROM golang:1.27-alpine AS agent
WORKDIR /src
COPY agent/elasticsearch-agent/ ./
ARG AGENT_VERSION=0.1.0
RUN for arch in amd64 arm64; do \
      CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build -trimpath \
        -ldflags "-s -w -X main.version=${AGENT_VERSION}" \
        -o /out/byoc-agent-linux-$arch ./cmd/byoc-agent || exit 1; \
    done

FROM ghcr.io/opentofu/opentofu:1.12.6-minimal AS tofu

FROM python:3.13-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.11.30 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin byoc
COPY --from=deps /opt/venv /opt/venv
COPY --from=tofu /usr/local/bin/tofu /usr/local/bin/tofu
COPY --from=agent /out/ /opt/byoc/agent/
COPY infrastructure/terraform/gcp/modules /opt/byoc/terraform/gcp/modules
WORKDIR /app
COPY backend/ /app/
RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /var/lib/byoc/workspaces /var/lib/byoc/plugin-cache \
    && chown -R byoc /var/lib/byoc
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TERRAFORM_BINARY=/usr/local/bin/tofu \
    TERRAFORM_MODULES_DIR=/opt/byoc/terraform/gcp/modules \
    TERRAFORM_PLUGIN_CACHE_DIR=/var/lib/byoc/plugin-cache \
    WORKSPACES_DIR=/var/lib/byoc/workspaces \
    AGENT_BINARIES_DIR=/opt/byoc/agent
USER byoc
EXPOSE 8000 9101
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
