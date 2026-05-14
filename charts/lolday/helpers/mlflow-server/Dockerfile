# H-21-img: upstream MLflow base pinned by digest. Bumping the tag
# (e.g., v2.20.3 -> v2.21.0) is a manual operator action (per
# .github/dependabot.yml comment) — Dependabot does not auto-PR this
# file. The digest pin makes any retag silently re-anchor visible.
FROM ghcr.io/mlflow/mlflow:v2.20.3@sha256:28639c6e142a5da4b91461e27f8473c6d13113838adcb3b9e09d3929cc3ec724

# Upstream ghcr.io mlflow image is slim and omits Postgres and S3 drivers.
# - psycopg2-binary: backend-store-uri `postgresql+psycopg2://...`
# - boto3: S3 artifact store (MinIO via MLFLOW_S3_ENDPOINT_URL)
#
# M-helper-hashes: hash-pinned requirements file generated via
# `uv pip compile --generate-hashes`. Pip refuses any install whose
# downloaded artifact hash differs from the pin.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# L-mlflow-user: drop privilege at image level. Upstream mlflow base
# leaves USER unset (= root). MLflow's runtime writes to its tracking
# store (Postgres via psycopg2) and its artifact store (MinIO via boto3);
# neither requires UID 0. Align with backend's `lolday` user (UID 1000).
# `|| true` covers a future base image that already has UID 1000 — useradd
# would exit 9 in that case; the existing user satisfies USER 1000 below.
# The chart's pod-level securityContext already requests runAsUser: 1000
# + fsGroup: 1000, so this layer makes the image self-consistent across
# any namespace + PSS profile, not just lolday's.
RUN useradd -m -u 1000 -s /usr/sbin/nologin mlflow || true
USER 1000
