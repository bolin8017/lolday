FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'maldet[lightning]>=2.0,<3.0'

WORKDIR /app
COPY maldet_validator.py .

USER 1000
ENTRYPOINT ["python", "-m"]
CMD ["maldet_validator"]
