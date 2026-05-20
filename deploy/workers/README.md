# GCP Celery Worker Deployment

These scripts start dedicated Celery worker containers on a GCE VM using the worker image built from `Dockerfile.worker`.

## Prerequisites

- A GCE VM with Docker installed.
- Access to the Google Artifact Registry repository that stores `leadership-worker`.
- Required environment variables loaded before running the scripts, ideally from a GCP Secret Manager sourced script.

## Build and push the worker image

Replace `REGION`, `PROJECT_ID`, and `REPO` with your actual Artifact Registry values:

```sh
docker build -f Dockerfile.worker -t REGION-docker.pkg.dev/PROJECT_ID/REPO/leadership-worker:latest .
docker push REGION-docker.pkg.dev/PROJECT_ID/REPO/leadership-worker:latest
```

## Run the startup scripts on the VM

SSH into the VM:

```sh
gcloud compute ssh YOUR_VM_NAME --zone YOUR_ZONE
```

Load your environment variables, then run the worker startup scripts as needed:

```sh
source /path/to/secret-manager-env.sh
bash deploy/workers/startup-signals.sh
bash deploy/workers/startup-analysis.sh
bash deploy/workers/startup-background.sh
```

## Check worker logs

```sh
docker logs -f celery-signals
```

## Verify workers are connected

```sh
docker exec celery-signals celery -A app.celery_app inspect ping
```
