FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

RUN set -eux; \
    find /etc/apt/sources.list.d -maxdepth 1 -type f -iname '*yarn*' -delete; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server; \
    rm -rf /var/lib/apt/lists/*
