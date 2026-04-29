FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml /app/
COPY job_helper/ /app/job_helper/

RUN pip install --no-cache-dir .

USER 1000

# No ENTRYPOINT: init containers specify `command` in the Pod spec.
