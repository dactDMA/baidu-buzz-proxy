# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.13-alpine3.24
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.0

FROM ${UV_IMAGE} AS uv

FROM alpine:3.24 AS baidu-pcs-go

ARG TARGETARCH
ARG BAIDU_PCS_GO_VERSION=4.0.1

RUN apk add --no-cache ca-certificates unzip wget \
    && case "${TARGETARCH}" in \
        amd64) asset_arch="amd64"; checksum="ffbf6d15eef8f7df06ad59d46e7347c71d725dec24d1f558357bd27d16aa65b3" ;; \
        arm64) asset_arch="arm64"; checksum="d1d5e0ba44cdfad219ea12749dce1103a07873d9adf89dcf64dafc47aea44e53" ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && archive="BaiduPCS-Go-v${BAIDU_PCS_GO_VERSION}-linux-${asset_arch}.zip" \
    && wget -q "https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/v${BAIDU_PCS_GO_VERSION}/${archive}" \
    && echo "${checksum}  ${archive}" | sha256sum -c - \
    && unzip -q "${archive}" -d /tmp/baidu-pcs-go \
    && install -Dm755 "$(find /tmp/baidu-pcs-go -type f -name BaiduPCS-Go -print -quit)" /out/BaiduPCS-Go

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN apk add --no-cache build-base libffi-dev

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.source="https://github.com/dactDMA/baidu-buzz-proxy" \
    org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache ca-certificates libffi tini tzdata \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && install -d -o app -g app /app/data

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=baidu-pcs-go /out/BaiduPCS-Go /usr/local/libexec/BaiduPCS-Go
COPY --chmod=755 deploy/app/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "baidu_buzz_proxy.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
