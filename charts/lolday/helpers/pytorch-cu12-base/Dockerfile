# lolday/pytorch-cu12-base — CVE-clean CUDA 12.6 + Python 3.12 + torch 2.7
#
# Why custom: pytorch/pytorch official runtime images pin Python 3.11 via
# conda, but lolday's islab-malware-detector requires Python 3.12+.
# Building on nvidia/cuda:12.6.* + deadsnakes python3.12 keeps both the
# CUDA layer and the Python layer upstream-managed.
#
# Trivy policy: platform blocks on CRITICAL only. CVE-2025-32434 (torch
# <2.6.0 RCE via pickle deserialisation) is the driver of the torch
# version pin — 2.6.0 is the fix; we use 2.7.0 for the CUDA 12.6 wheel
# set + current maintenance branch.
#
# Reproducibility: every pip package is pinned. A rebuild a year from
# now lands on the same resolver set; no floating "latest" that could
# introduce fresh High/Critical CVEs between a successful Trivy scan
# and a later rebuild.

FROM nvidia/cuda:13.2.1-cudnn-runtime-ubuntu22.04

# Python 3.12 via deadsnakes PPA — fastest path on Ubuntu 22.04 LTS to
# get a maintained 3.12 interpreter without compiling from source.
# Runs `apt-get upgrade` so that base-image CVEs landed after the
# nvidia/cuda:12.6.3 tag was cut are patched at build time.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
         software-properties-common ca-certificates curl \
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

# Torch 2.7.0 with CUDA 12.6 wheel set (matches host driver 560 which
# caps at CUDA 12.6). torch >=2.6 required for CVE-2025-32434.
RUN pip install --no-cache-dir \
        torch==2.7.0 \
        torchvision==0.22.0 \
        --index-url https://download.pytorch.org/whl/cu126

# Baseline scientific stack every detector uses. All pinned exact so
# the Trivy scan of this base image is reproducible across rebuilds.
RUN pip install --no-cache-dir \
        numpy==2.2.3 \
        pandas==2.2.3 \
        scikit-learn==1.6.1 \
        pyelftools==0.32 \
        structlog==25.1.0 \
        typer==0.15.2 \
        "islab-malware-detector[mlflow]==0.5.0"

# Default non-root user expected by lolday's detector pod spec.
RUN useradd -m -u 1000 lolday
USER 1000
WORKDIR /app
