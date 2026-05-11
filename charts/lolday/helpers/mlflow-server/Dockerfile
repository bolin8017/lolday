FROM ghcr.io/mlflow/mlflow:v2.20.3

# Upstream ghcr.io mlflow image is slim and omits Postgres and S3 drivers.
# - psycopg2-binary: backend-store-uri `postgresql+psycopg2://...`
# - boto3: S3 artifact store (MinIO via MLFLOW_S3_ENDPOINT_URL)
RUN pip install --no-cache-dir psycopg2-binary==2.9.9 boto3==1.38.5
