# The verified release workflow passes a digest-pinned Python base reference.
# Developer builds keep the default; neither base nor source label authorizes release.
ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}

ARG SOURCE_REVISION=unverified-local-build
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/scottjoyner/auto-router"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --upgrade pip && pip install .

EXPOSE 8088

CMD ["uvicorn", "auto_router.secure_live:app", "--host", "0.0.0.0", "--port", "8088"]
