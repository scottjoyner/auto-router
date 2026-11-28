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

# Run as an unprivileged user. UID/GID 1000 matches the usual owner of the
# bind-mounted ./data and ./artifacts directories; operators using a different
# host UID must make those directories writable by this user.
RUN groupadd --gid 1000 router \
    && useradd --uid 1000 --gid router --create-home --shell /usr/sbin/nologin router \
    && mkdir -p /data /artifacts \
    && chown -R router:router /app /data /artifacts

USER router

EXPOSE 8088

CMD ["uvicorn", "auto_router.secure_live:app", "--host", "0.0.0.0", "--port", "8088"]
