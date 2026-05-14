FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

WORKDIR /app
COPY pyproject.toml /app/
COPY job_helper/ /app/job_helper/

RUN pip install --no-cache-dir .

USER 1000

# No ENTRYPOINT: init containers specify `command` in the Pod spec.
