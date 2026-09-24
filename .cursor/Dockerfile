# Base image for the Autonomous Futures Paper-Trading System Cloud Agent.
# Pins Python 3.13 to match .python-version and the CI matrix (see
# .github/workflows/ci.yml). System packages only — project dependencies are
# installed from the checked-out source by .cursor/install.sh.
FROM python:3.13-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Provide a non-root user that matches the rest of the tooling.
RUN useradd --create-home --shell /bin/bash ubuntu

USER ubuntu
WORKDIR /home/ubuntu
