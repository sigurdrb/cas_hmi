# syntax=docker/dockerfile:1
#
# Builds for linux/amd64, linux/arm64 and linux/arm/v7:
#   docker buildx build --platform linux/arm/v7 -t cas-hmi:armv7 --load .

# trixie: piwheels builds its cp313 wheels on Raspberry Pi OS trixie, so the libraries match.
FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# PyPI has no arm/v7 wheel for cryptography (pulled in by asyncua), and building it
# needs a Rust toolchain. piwheels has prebuilt armv7l wheels, so use it on 32-bit arm.
# --prefer-binary keeps pip on the newest version that has a wheel, instead of a newer
# sdist that piwheels has not built yet; --only-binary makes a missing wheel fail fast.
ARG TARGETARCH
COPY requirements.txt .
RUN if [ "$TARGETARCH" = "arm" ]; then \
        EXTRA_INDEX="--extra-index-url https://www.piwheels.org/simple"; \
    fi; \
    pip install --prefer-binary --only-binary=cryptography $EXTRA_INDEX -r requirements.txt

COPY cashmi ./cashmi

RUN useradd --system --no-create-home cashmi
USER cashmi

EXPOSE 8080

# Bind to all interfaces inside the container and never try to open a browser.
# Pass --url (and any other flag) as arguments to `docker run`.
ENTRYPOINT ["python", "-m", "cashmi", "--host", "0.0.0.0", "--no-browser"]
