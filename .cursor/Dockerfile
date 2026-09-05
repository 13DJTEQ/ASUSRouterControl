FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System toolchain. git and curl are required in fully custom images;
# build-essential covers any dependency that must build a wheel from source;
# sudo mirrors the default Cloud Agent image so the agent user can escalate.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        build-essential \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Recreate the platform's default non-root user (uid/gid 1000) with
# passwordless sudo so the environment matches the default Cloud Agent image.
RUN groupadd --gid 1000 ubuntu \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu

USER ubuntu
WORKDIR /home/ubuntu
