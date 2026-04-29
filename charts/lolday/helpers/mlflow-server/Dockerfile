FROM ghcr.io/mlflow/mlflow:v2.20.3

# Upstream ghcr.io mlflow image is slim and omits Postgres driver.
# Bake psycopg2-binary so the server can use the backend-store-uri
# `postgresql+psycopg2://...` without needing runtime installs.
RUN pip install --no-cache-dir psycopg2-binary==2.9.9

# ci-verify 2026-04-30 — should not trigger helpers.yml
