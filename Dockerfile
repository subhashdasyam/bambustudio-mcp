# Preset storage and MCP discovery work without Studio; slicing needs a supported runtime.
FROM docker.io/library/python:3.12-slim
ARG MCP_UID=65532
ARG MCP_GID=65532
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY src/ ./src/
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$MCP_GID" mcp \
    && useradd --uid "$MCP_UID" --gid "$MCP_GID" --create-home --shell /usr/sbin/nologin mcp \
    && pip install --no-cache-dir .
ENV HOME=/home/mcp
ENV BAMBUSTUDIO_WORKSPACE=/tmp/bambustudio-mcp
USER mcp
CMD ["bambustudio-mcp"]
