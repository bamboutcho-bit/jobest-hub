# =============================================================================
# ALL-IN-ONE image: Postgres + Ollama + Playwright browser server + the app
# (pipeline loop, inbox monitor loop, dashboard) in a single container,
# managed by supervisord.
#
# This trades isolation for simplicity: one `docker build` + one `docker run`.
# For anything beyond local/single-user use, the docker-compose.microservices.yml
# + Dockerfile.microservices setup (separate containers) is the safer choice —
# it can restart/scale each piece independently and doesn't run Postgres and
# your scraping/auto-apply logic inside the same failure domain.
# =============================================================================

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PG_VERSION=16 \
    PGDATA=/var/lib/postgresql/data \
    OLLAMA_MODELS=/root/.ollama \
    APP_HOME=/app

# -----------------------------------------------------------------------------
# System packages: Python, Postgres, Node (for the Playwright server),
# supervisor to run everything, and the OS deps Playwright's Chromium needs.
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg lsb-release zstd \
        python3 python3-pip python3-venv \
        postgresql postgresql-contrib \
        supervisor \
        nodejs npm \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Ollama (official install script; installs the `ollama` CLI/server binary)
# -----------------------------------------------------------------------------
RUN curl -fsSL https://ollama.com/install.sh | sh

# -----------------------------------------------------------------------------
# Python dependencies
# -----------------------------------------------------------------------------
WORKDIR ${APP_HOME}
COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

# Playwright's own browser binaries + the OS libraries they need.
RUN python3 -m playwright install --with-deps chromium

# -----------------------------------------------------------------------------
# App code
# -----------------------------------------------------------------------------
COPY . .
# Originals kept for reference; not needed inside the image.
RUN rm -f Dockerfile.microservices docker-compose.microservices.yml

# -----------------------------------------------------------------------------
# supervisord + entrypoint
# -----------------------------------------------------------------------------
COPY docker/supervisord.conf /etc/supervisor/conf.d/jobsearch.conf
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/wait-for-deps.sh /usr/local/bin/wait-for-deps.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/wait-for-deps.sh

# Directories that get mounted as volumes / written to at runtime.
RUN mkdir -p ${PGDATA} /var/log/supervisor /app/outreach_drafts \
    && chown -R postgres:postgres ${PGDATA}

EXPOSE 8080

VOLUME ["/var/lib/postgresql/data", "/root/.ollama", "/app/outreach_drafts"]

ENTRYPOINT ["/entrypoint.sh"]
