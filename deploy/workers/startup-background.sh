#!/bin/bash
set -e

# Replace REGION, PROJECT_ID, and REPO with your actual Artifact Registry values.
# Load environment variables from a GCP Secret Manager sourced script before running this.

# Pull latest image from Google Artifact Registry
docker pull REGION-docker.pkg.dev/PROJECT_ID/REPO/leadership-worker:latest

# Stop existing container if running
docker stop celery-background 2>/dev/null || true
docker rm celery-background 2>/dev/null || true

# Start background worker
docker run -d \
  --name celery-background \
  --restart unless-stopped \
  -e CELERY_BROKER_URL="${CELERY_BROKER_URL}" \
  -e CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND}" \
  -e REDIS_URL="${REDIS_URL}" \
  -e DATABASE_URL="${DATABASE_URL}" \
  -e GEMINI_RPM_LIMIT="${GEMINI_RPM_LIMIT:-60}" \
  REGION-docker.pkg.dev/PROJECT_ID/REPO/leadership-worker:latest \
  celery -A app.celery_app worker -Q embeddings,scraping -c 2 --loglevel=info
