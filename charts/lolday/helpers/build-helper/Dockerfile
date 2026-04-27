FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'maldet[lightning]>=1.0,<2.0'

WORKDIR /app
COPY maldet_validator.py .

USER 1000
ENTRYPOINT ["python", "-m"]
CMD ["maldet_validator"]
