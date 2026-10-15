FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências do sistema + repositório oficial do PostgreSQL para pg_dump 18.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libreoffice \
        libreoffice-writer \
        chromium \
        chromium-driver \
        fonts-dejavu-core \
        fonts-liberation \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && printf "Types: deb\nURIs: https://apt.postgresql.org/pub/repos/apt\nSuites: %s-pgdg\nArchitectures: %s\nComponents: main\nSigned-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc\n" \
        "$VERSION_CODENAME" "$(dpkg --print-architecture)" \
        > /etc/apt/sources.list.d/pgdg.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . /app/
