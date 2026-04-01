# MeshForge Maps - Docker Container
#
# Build:   docker build -t meshforge-maps .
# Run:     docker run -p 8808:8808 -p 8809:8809 meshforge-maps
#
# Configure via environment variables:
#   docker run -p 8808:8808 -p 8809:8809 \
#     -e MQTT_BROKER=mqtt.meshtastic.org \
#     -e MQTT_TOPIC=msh/US/HI \
#     -e API_KEY=your-secret-key \
#     meshforge-maps
#
# Or mount a settings.json:
#   docker run -p 8808:8808 -p 8809:8809 \
#     -v /path/to/settings.json:/home/meshforge/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json \
#     meshforge-maps

FROM python:3.12-slim

LABEL maintainer="Nursedude <nursedude@example.invalid>"
LABEL description="MeshForge Maps - Unified mesh network visualization"
LABEL org.opencontainers.image.source="https://github.com/Nursedude/meshforge-maps"

# Create non-root user
RUN useradd -m -s /bin/bash meshforge

# Install system dependencies
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /opt/meshforge-maps

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data/config directories
RUN mkdir -p /home/meshforge/.config/meshforge/plugins/org.meshforge.extension.maps \
             /home/meshforge/.local/share/meshforge \
             /home/meshforge/.cache/meshforge && \
    chown -R meshforge:meshforge /home/meshforge /opt/meshforge-maps

# Environment
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Entrypoint script handles env var -> settings.json mapping
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

USER meshforge

EXPOSE 8808 8809

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8808/api/status || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "-m", "src.main", "--host", "0.0.0.0"]
