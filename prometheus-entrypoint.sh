#!/bin/sh
set -e

# Create necessary directories
mkdir -p /prometheus/data

# Ensure correct permissions
chown -R nobody:nobody /prometheus --quiet || true

# Start Prometheus
exec /bin/prometheus "$@"
