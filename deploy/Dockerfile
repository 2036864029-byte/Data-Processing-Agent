# DataAgent 运行镜像（uv 固定依赖；镜像内只允许 uv，G7）
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 先装依赖（缓存层），再装本项目源码
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config.example.yaml ./config.yaml
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080

# CLI 是唯一入口；单容器默认同时拉起 API 与 worker。
# 拆分为 api / worker 两个服务时使用 --role api / --role worker。
CMD ["dataagent", "up", "--host", "0.0.0.0", "--port", "8080", "--role", "all"]
