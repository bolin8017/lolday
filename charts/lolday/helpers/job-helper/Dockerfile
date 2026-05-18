FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

COPY requirements.txt /tmp/requirements.txt

# M-helper-hashes mirror (see build-helper/Dockerfile + §10 #38):
# --require-hashes makes pip refuse any install whose downloaded
# wheel/sdist hash differs from the value pinned in requirements.txt.
# Generated via:
#   cd charts/lolday/helpers/job-helper
#   uv pip compile --generate-hashes -o requirements.txt pyproject.toml
# Switching off the prior `pip install --no-cache-dir .` against bare
# pyproject.toml closes the runtime-safety gap noted in §10 #38: that
# install resolved fresh from pyproject's >= ranges every build, but
# Docker's layer cache reused the layer when the COPY input was
# unchanged, so transitive security bumps to `uv.lock` produced a
# byte-identical image. With this layout, requirements.txt is the
# COPY input AND the install source — every bumped dep changes the
# digest.
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /app
COPY pyproject.toml /app/
COPY job_helper/ /app/job_helper/

# Install the local package without its deps (they were just installed
# from requirements.txt with hash pins). --no-deps ensures pip cannot
# pull a transitive that escaped the pinned set.
RUN pip install --no-cache-dir --no-deps .

USER 1000

# No ENTRYPOINT: init containers specify `command` in the Pod spec.
