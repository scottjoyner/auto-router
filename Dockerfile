FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --upgrade pip && pip install .

# COPY preserves checkout modes; the fleet's permissive umask can yield 0664.
# Bake a non-group-writable provider config or the source-identity gate must HOLD.
RUN chmod 0644 /app/config/providers.yaml

EXPOSE 8088

CMD ["uvicorn", "auto_router.secure_live:app", "--host", "0.0.0.0", "--port", "8088"]
