# lolday/pytorch-cu12-base — CVE-clean CUDA 12.6 + Python 3.12 + torch 2.7
#
# Why custom: pytorch/pytorch official runtime images pin Python 3.11 via
# conda, but lolday's islab-malware-detector requires Python 3.12+.
# Building on nvidia/cuda:12.6.* base + python.org's 3.12 keeps things
# upstream-managed for both the CUDA layer and the Python layer.
#
# Trivy-scanned expectation: 0 Critical. High CVEs typically come from
# transitively-pulled nvidia-cu12 wheels whose upstream patches lag;
# platform policy blocks on Critical only.

FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

# Python 3.12 via deadsnakes PPA — fastest path on Ubuntu 22.04 LTS to
# get a maintained 3.12 interpreter without compiling from source.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common ca-certificates curl \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         python3.12 python3.12-venv python3.12-dev \
         libgomp1 \
    && ln -sf /usr/bin/python3.12 /usr/local/bin/python \
    && ln -sf /usr/bin/python3.12 /usr/local/bin/python3 \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12 \
    && ln -sf /usr/local/bin/pip3 /usr/local/bin/pip \
    && apt-get purge -y software-properties-common \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Torch 2.7 — CUDA 12.6 wheel published by PyTorch. Matches host driver
# 560 (CUDA 12.6 max) and is the earliest torch release that does NOT
# carry CVE-2025-32434.
RUN pip install --no-cache-dir \
        torch==2.7.0 torchvision --index-url https://download.pytorch.org/whl/cu126

# Optional: baseline scientific stack that every detector uses. Pulling
# these into the base means detector images only need to add their own
# weight code, keeping per-detector layers tiny.
RUN pip install --no-cache-dir \
        numpy pandas scikit-learn pyelftools structlog typer \
        "islab-malware-detector[mlflow]>=0.5.0"

# Default non-root user expected by lolday's detector pod spec.
RUN useradd -m -u 1000 lolday
USER 1000
WORKDIR /app
