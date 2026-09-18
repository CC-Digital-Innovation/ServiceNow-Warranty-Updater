# Builder stage.
FROM python:3.14.7-alpine3.24 AS builder

WORKDIR /app

# Set environment variables to reduce writing to disk and improve performance.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Upgrade pip and install requirements.
COPY requirements.txt .
RUN python -m pip install pip==26.2.1 && \
    python -m pip install --no-cache-dir -r requirements.txt


# Runtime stage.
FROM alpine:3.24.2 AS runtime

LABEL org.opencontainers.image.authors="Anthony Farina"

WORKDIR /app

# Patch alpine packages and install clean Python runtime (without pip).
RUN apk update && apk upgrade --no-cache && \
    apk add --no-cache python3

# Set environment variables to reduce writing to disk and improve performance.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy app's dependencies.
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages

# Let Python know where the app's dependencies are located.
ENV PYTHONPATH="/usr/local/lib/python3.14/site-packages"

# Remove pip and setuptools from the runtime image to reduce size and vulnerabilities.
RUN rm -rf \
    /usr/local/lib/python3.14/site-packages/pip* \
    /usr/local/lib/python3.14/site-packages/setuptools*

# Copy source code.
COPY ./src .

# Set non-root user and group to run the app.
RUN addgroup -S -g 10015 servicenow_warranty_updater && \
    adduser -S -u 10014 -G servicenow_warranty_updater servicenow_warranty_updater
USER servicenow_warranty_updater:servicenow_warranty_updater

# Set the entry for the container to run the app.
ENTRYPOINT ["python", "servicenow_warranty_updater.py"]
