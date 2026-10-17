# Glama introspection only. Slicing still requires a supported BambuStudio runtime.
FROM docker.io/library/python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .
ENV BAMBUSTUDIO_WORKSPACE=/tmp/bambustudio-mcp
USER 65532:65532
CMD ["bambustudio-mcp"]
