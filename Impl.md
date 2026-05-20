# Implementation Guide & Cursor Task Breakdown
## Celery + RabbitMQ + Redis — Leadership Assessment Platform

> **How to use this document:**
> Part 1 is for you — the human. Read it once, follow it step by step, verify each service is alive before moving on.
> Part 2 is for Cursor — one task at a time, copy the prompt exactly, do not proceed to the next task until Cursor confirms the current one is complete and you have tested it.

---

## Table of Contents

**Part 1 — Environment Setup (You Do This)**
- [1. Understanding the Deployment Split](#1-understanding-the-deployment-split)
- [2. Local Windows Setup — What You Install](#2-local-windows-setup)
- [3. GCP Setup — What Gets Deployed](#3-gcp-setup)
- [4. Verifying Every Service Is Alive](#4-verifying-every-service-is-alive)
- [5. Environment Variables Reference](#5-environment-variables-reference)

**Part 2 — Cursor Task Breakdown**
- [Task Hierarchy Overview](#task-hierarchy-overview)
- [PHASE 0 — Foundation](#phase-0--foundation)
  - [Task 0.1 — Dependencies](#task-01--dependencies)
  - [Task 0.2 — Celery App Instance](#task-02--celery-app-instance)
  - [Task 0.3 — Celery Configuration](#task-03--celery-configuration)
  - [Task 0.4 — Docker Compose Services](#task-04--docker-compose-services)
- [PHASE 1 — Redis Layer](#phase-1--redis-layer)
  - [Task 1.1 — Redis Client Module](#task-11--redis-client-module)
  - [Task 1.2 — Task Registry (replaces in-memory dicts)](#task-12--task-registry)
  - [Task 1.3 — Read Cache Module](#task-13--read-cache-module)
  - [Task 1.4 — Rate Limiter Module](#task-14--rate-limiter-module)
- [PHASE 2 — Celery Tasks](#phase-2--celery-tasks)
  - [Task 2.1 — Sync DB Session for Workers](#task-21--sync-db-session-for-workers)
  - [Task 2.2 — Signal Extraction Task](#task-22--signal-extraction-task)
  - [Task 2.3 — Final Analysis Task](#task-23--final-analysis-task)
  - [Task 2.4 — Assessment Creation Task Chain](#task-24--assessment-creation-task-chain)
  - [Task 2.5 — Embedding Generation Task](#task-25--embedding-generation-task)
  - [Task 2.6 — Legacy Analysis Task Wrapper](#task-26--legacy-analysis-task-wrapper)
- [PHASE 3 — FastAPI Integration](#phase-3--fastapi-integration)
  - [Task 3.1 — Modify intelligence.py (Remove In-Memory State)](#task-31--modify-intelligencepy)
  - [Task 3.2 — Modify agent/routes.py (WebSocket Handler)](#task-32--modify-agentroutespy)
  - [Task 3.3 — Modify assessment_registry.py (Assessment Creation)](#task-33--modify-assessment_registrypy)
  - [Task 3.4 — Modify routers/intelligence.py (Status + Rerun Endpoints)](#task-34--modify-routersintelligencepy)
  - [Task 3.5 — Modify routers/candidate.py (Cached Reads)](#task-35--modify-routerscandidatepy)
- [PHASE 4 — Cache Invalidation](#phase-4--cache-invalidation)
  - [Task 4.1 — Invalidation Hooks](#task-41--invalidation-hooks)
- [PHASE 5 — GCP Deployment](#phase-5--gcp-deployment)
  - [Task 5.1 — Dockerfile for Celery Workers](#task-51--dockerfile-for-celery-workers)
  - [Task 5.2 — GCP Cloud Run / GCE Worker Services](#task-52--gcp-deployment-config)
- [PHASE 6 — Observability](#phase-6--observability)
  - [Task 6.1 — Flower Monitoring Setup](#task-61--flower-monitoring)
  - [Task 6.2 — Dead Letter Queue Alert Handler](#task-62--dlq-alert-handler)
- [PHASE 7 — Testing](#phase-7--testing)
  - [Task 7.1 — Unit Tests for Celery Tasks](#task-71--unit-tests)
  - [Task 7.2 — Integration Test: Full Interview Pipeline](#task-72--integration-test)

---

# PART 1 — ENVIRONMENT SETUP

## 1. Understanding the Deployment Split

Before installing anything, understand where each service lives:

```
YOUR WINDOWS MACHINE (Development)
├── Your code editor (Cursor)
├── Docker Desktop for Windows
│   ├── RabbitMQ container     ← message broker
│   ├── Redis container        ← cache + result backend
│   └── Celery workers         ← run from Docker locally
├── FastAPI dev server         ← runs locally via uvicorn
└── .env file                  ← all connection strings

GCP (Production)
├── Cloud Run or GCE           ← FastAPI application
├── Cloud Memorystore (Redis)  ← managed Redis
├── CloudAMQP or RabbitMQ VM  ← managed or self-hosted RabbitMQ
├── GCE VM or Cloud Run Jobs   ← Celery workers
└── Cloud SQL (PostgreSQL)     ← existing DB (unchanged)
```

You develop and test everything locally via Docker Desktop. GCP only gets touched in Phase 5. Do not touch GCP until Phase 5 is complete.

---

## 2. Local Windows Setup

### 2.1 Install Docker Desktop

If you do not already have it:

1. Download from https://www.docker.com/products/docker-desktop/
2. Install and restart Windows.
3. Open Docker Desktop and make sure the Docker engine is running (green icon in the system tray).
4. Open PowerShell and confirm:

```powershell
docker --version
docker compose version
```

Both must return version numbers. If `docker compose` fails, try `docker-compose` (older syntax).

### 2.2 Install Redis CLI (for local testing from Windows)

You do not need Redis installed on Windows itself — it runs in Docker. But having the CLI available for debugging is helpful.

Option A — Use Redis inside Docker (recommended, zero install):
```powershell
docker exec -it redis-local redis-cli
```

Option B — Install via Chocolatey if you prefer a native CLI:
```powershell
choco install redis-64
```

### 2.3 Install RabbitMQ Management CLI (optional, for debugging)

The RabbitMQ management UI (port 15672) handles everything you need visually. No CLI needed locally.

### 2.4 Python Dependencies (in your existing virtualenv)

In your project directory, with your existing virtualenv activated:

```powershell
pip install celery[rabbitmq]==5.3.6
pip install redis==5.0.3
pip install kombu==5.3.4
pip install flower==2.0.1
pip install psycopg2-binary==2.9.9
```

> `psycopg2-binary` is required because Celery workers need a **synchronous** DB driver. Your FastAPI code uses `asyncpg` — that stays. Workers get their own sync connection.

Confirm everything installed:
```powershell
pip show celery redis kombu flower psycopg2-binary
```

Add all of these to your `requirements.txt`.

---

## 3. GCP Setup

### 3.1 Redis — Cloud Memorystore

Cloud Memorystore is Google's managed Redis. It stays inside your VPC, so Cloud Run and GCE workers reach it without exposing it to the internet.

```bash
# Run from Cloud Shell or gcloud CLI on your machine

gcloud redis instances create leadership-redis \
  --size=2 \
  --region=YOUR_REGION \
  --redis-version=redis_7_0 \
  --tier=STANDARD_HA

# Get the connection IP (no port needed, default is 6379)
gcloud redis instances describe leadership-redis --region=YOUR_REGION \
  --format="get(host)"
```

Save the IP — it becomes `REDIS_URL=redis://IP:6379/0` in your production `.env`.

> Standard HA tier gives you automatic failover. For a hiring platform, use it.

### 3.2 RabbitMQ — CloudAMQP (Recommended for GCP)

Self-hosting RabbitMQ on GCE is viable but adds operational burden. CloudAMQP is a managed RabbitMQ service that runs in the same GCP region as your app.

1. Go to https://www.cloudamqp.com
2. Create an account → Create New Instance
3. Select plan: **Lemur (free)** for development, **Little Lemur** ($19/mo) for production
4. Select GCP region matching your Cloud Run region
5. Copy the AMQP URL from the instance dashboard: `amqp://user:password@host/vhost`

Save this as `CELERY_BROKER_URL` in your production secrets.

Alternative — Self-host on GCE:
```bash
gcloud compute instances create rabbitmq-vm \
  --machine-type=e2-medium \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --zone=YOUR_ZONE

# SSH in and install
sudo apt-get install rabbitmq-server -y
sudo rabbitmq-plugins enable rabbitmq_management
sudo rabbitmqctl add_user user password
sudo rabbitmqctl set_user_tags user administrator
sudo rabbitmqctl set_permissions -p / user ".*" ".*" ".*"
```

### 3.3 Celery Workers — GCE or Cloud Run Jobs

Celery workers are long-running processes. Cloud Run is designed for request/response, not long-running daemons. Use either:

- **GCE VM** (simplest): Run workers as a systemd service on a VM.
- **GKE** (best for scale): Deploy workers as a Deployment with HPA.
- **Cloud Run Jobs** (acceptable): Trigger jobs, but not ideal for persistent workers.

For now, plan for GCE. The Dockerfile and deployment config are covered in Task 5.1 and 5.2.

---

## 4. Verifying Every Service Is Alive

Run this after setting up Docker locally. This is your checklist before writing a single line of application code.

### Step 1 — Start local services

Create this file at the root of your project as `docker-compose.dev.yml`:

```yaml
version: "3.9"

services:
  rabbitmq:
    image: rabbitmq:3.12-management
    container_name: rabbitmq-local
    ports:
      - "5672:5672"
      - "15672:15672"
    environment:
      RABBITMQ_DEFAULT_USER: devuser
      RABBITMQ_DEFAULT_PASS: devpass
    volumes:
      - rabbitmq_dev_data:/var/lib/rabbitmq

  redis:
    image: redis:7.2-alpine
    container_name: redis-local
    ports:
      - "6379:6379"
    command: redis-server --save 60 1 --loglevel warning
    volumes:
      - redis_dev_data:/data

volumes:
  rabbitmq_dev_data:
  redis_dev_data:
```

```powershell
docker compose -f docker-compose.dev.yml up -d
```

### Step 2 — Verify RabbitMQ

Open your browser: http://localhost:15672
- Login: `devuser` / `devpass`
- You should see the RabbitMQ management dashboard.
- Go to **Queues** tab — it should be empty (no queues yet, that is correct).

From PowerShell:
```powershell
docker exec rabbitmq-local rabbitmqctl status
```
Should print server info without errors.

### Step 3 — Verify Redis

```powershell
docker exec -it redis-local redis-cli ping
```
Expected response: `PONG`

```powershell
docker exec -it redis-local redis-cli info server
```
Should print Redis server info.

### Step 4 — Verify Celery Can Connect (after Phase 0 is complete in code)

Once Task 0.2 and 0.3 are done (Celery app and config exist in code):

```powershell
# From your project root with virtualenv active
celery -A app.celery_app inspect ping
```

Expected output:
```
-> celery@your-hostname: OK
   pong
```

If you see `Error: No nodes replied`, the broker URL in your `.env` is wrong or RabbitMQ is not running.

### Step 5 — Verify Flower Dashboard (after workers are running)

```powershell
celery -A app.celery_app flower --port=5555
```

Open: http://localhost:5555
You should see the Flower dashboard with worker status.

---

## 5. Environment Variables Reference

Add these to your `.env` file. Never commit `.env` to git.

```bash
# ─── Celery / RabbitMQ ────────────────────────────────────────────────────────
CELERY_BROKER_URL=amqp://devuser:devpass@localhost:5672//
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# ─── Redis ────────────────────────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ─── Gemini Rate Limit (tune to your API quota tier) ─────────────────────────
GEMINI_RPM_LIMIT=60

# ─── Worker Concurrency (tune per machine CPU count) ─────────────────────────
CELERY_SIGNALS_CONCURRENCY=8
CELERY_ANALYSIS_CONCURRENCY=4
CELERY_BACKGROUND_CONCURRENCY=2

# ─── Production overrides (GCP) ───────────────────────────────────────────────
# CELERY_BROKER_URL=amqp://user:password@cloudamqp-host/vhost
# CELERY_RESULT_BACKEND=redis://MEMORYSTORE_IP:6379/0
# REDIS_URL=redis://MEMORYSTORE_IP:6379/0
```

---

# PART 2 — CURSOR TASK BREAKDOWN

## Task Hierarchy Overview

Tasks must be completed in phase order. Within a phase, numbered tasks must be completed in order. Never skip a phase.

```
PHASE 0 — Foundation (infrastructure scaffolding, no logic yet)
│   Task 0.1 — Install and pin dependencies
│   Task 0.2 — Create Celery app instance
│   Task 0.3 — Create Celery configuration
│   Task 0.4 — Add Docker Compose dev services
│   └── CHECKPOINT: Celery can connect to RabbitMQ and Redis
│
PHASE 1 — Redis Layer (all Redis modules, no task logic yet)
│   Task 1.1 — Redis client module
│   Task 1.2 — Task registry (replaces _PENDING_SIGNAL_TASKS and _ANALYSIS_TASKS)
│   Task 1.3 — Read cache module
│   Task 1.4 — Rate limiter module
│   └── CHECKPOINT: Redis module unit tests pass
│
PHASE 2 — Celery Tasks (worker logic, no FastAPI changes yet)
│   Task 2.1 — Sync DB session for workers
│   Task 2.2 — Signal extraction task
│   Task 2.3 — Final analysis task
│   Task 2.4 — Assessment creation task chain
│   Task 2.5 — Embedding generation task
│   Task 2.6 — Legacy analysis task wrapper
│   └── CHECKPOINT: Tasks can be dispatched manually and appear in RabbitMQ
│
PHASE 3 — FastAPI Integration (modifying existing routes and services)
│   Task 3.1 — Modify intelligence.py (remove in-memory state)
│   Task 3.2 — Modify agent/routes.py (WebSocket handler)
│   Task 3.3 — Modify assessment_registry.py (assessment creation routes)
│   Task 3.4 — Modify routers/intelligence.py (status + rerun endpoints)
│   Task 3.5 — Modify routers/candidate.py (cached reads)
│   └── CHECKPOINT: Full interview flow works end-to-end locally
│
PHASE 4 — Cache Invalidation (correctness hooks)
│   Task 4.1 — Add cache invalidation calls at every mutation point
│   └── CHECKPOINT: Stale cache tests pass
│
PHASE 5 — GCP Deployment (production infrastructure)
│   Task 5.1 — Dockerfile for Celery workers
│   Task 5.2 — GCP deployment configuration
│   └── CHECKPOINT: Workers running on GCE, connected to Memorystore + CloudAMQP
│
PHASE 6 — Observability (monitoring and alerting)
│   Task 6.1 — Flower monitoring setup
│   Task 6.2 — Dead letter queue alert handler
│   └── CHECKPOINT: Flower shows all workers, DLQ handler logs failures
│
PHASE 7 — Testing (validation before going live)
    Task 7.1 — Unit tests for Celery tasks
    Task 7.2 — Integration test: full interview pipeline
    └── CHECKPOINT: All tests pass, system ready for production traffic
```

---

## PHASE 0 — Foundation

---

### Task 0.1 — Dependencies

**What this does:** Adds Celery, Redis, Kombu, Flower, and psycopg2 to the project. This is the only task that touches `requirements.txt` and `pyproject.toml` (if used). No application code is written yet.

**Cursor Prompt:**

```
I am adding Celery, RabbitMQ (via Kombu), Redis, Flower, and psycopg2-binary to my FastAPI project.
This is a leadership assessment platform using FastAPI, PostgreSQL (asyncpg + SQLAlchemy async), and Gemini.

Do the following:

1. Add these exact packages and versions to requirements.txt:
   - celery[rabbitmq]==5.3.6
   - redis==5.0.3
   - kombu==5.3.4
   - flower==2.0.1
   - psycopg2-binary==2.9.9

2. Do NOT remove or change any existing packages in requirements.txt.

3. If the project uses pyproject.toml instead of or in addition to requirements.txt, add the same packages there under [project.dependencies] or [tool.poetry.dependencies] depending on which format is already in use.

4. Do NOT install anything, do NOT run pip, do NOT modify any Python files. Only update the dependency files.

5. After updating, print the final state of requirements.txt so I can verify it is correct.
```

---

### Task 0.2 — Celery App Instance

**What this does:** Creates the `app/celery_app.py` file — the single Celery application object that every task and every worker will import. Nothing else imports it yet.

**Cursor Prompt:**

```
Create a new file at app/celery_app.py for my FastAPI leadership assessment project.

This file must:

1. Define a single Celery application instance called `celery_app` using the Celery class from the celery package.

2. The app name must be "leadership_assessment".

3. Configure it to load configuration from "app.celery_config" using app.config_from_object().

4. Call app.autodiscover_tasks() with the following task module paths:
   - "app.tasks.signals"
   - "app.tasks.analysis"
   - "app.tasks.assessment"
   - "app.tasks.legacy"

5. Do NOT define any tasks in this file.

6. Do NOT import anything from the rest of the application (no models, no services, no routers) in this file. Keep it completely isolated.

7. Add a short comment at the top explaining: "This is the central Celery application instance. Import celery_app from here in tasks and worker startup commands."

The file should be clean, minimal, and only do exactly what is described.
```

---

### Task 0.3 — Celery Configuration

**What this does:** Creates `app/celery_config.py` — all broker URLs, queue definitions, routing, retry policies, and serialization settings. This is the brain of the entire Celery setup.

**Cursor Prompt:**

```
Create a new file at app/celery_config.py for my FastAPI leadership assessment project.

This file configures the Celery application. Read all values from environment variables using os.environ.get() with sensible defaults. Do NOT hardcode any credentials.

The file must configure the following:

BROKER AND BACKEND:
- broker_url: read from env var CELERY_BROKER_URL, default "amqp://devuser:devpass@localhost:5672//"
- result_backend: read from env var CELERY_RESULT_BACKEND, default "redis://localhost:6379/0"

SERIALIZATION:
- task_serializer = "json"
- result_serializer = "json"
- accept_content = ["json"]
- timezone = "UTC"
- enable_utc = True

RELIABILITY:
- task_acks_late = True  (only acknowledge after task finishes, not when received)
- worker_prefetch_multiplier = 1  (one task at a time per worker slot)
- task_reject_on_worker_lost = True  (requeue if worker dies mid-task)

RESULTS:
- result_expires = 86400  (24 hours)
- task_ignore_result = False

QUEUES AND ROUTING:
Define four queues using kombu.Queue and kombu.Exchange:
- Queue name "signals"   → for signal extraction tasks (high priority, on the interview critical path)
- Queue name "analysis"  → for final analysis and assessment generation tasks
- Queue name "embeddings"→ for RAG embedding generation (low priority, background)
- Queue name "scraping"  → for LinkedIn scraping tasks

Each queue except "embeddings" and "scraping" must have:
- x-dead-letter-exchange pointing to a "dlx" exchange
- x-dead-letter-routing-key of "dlq"
- x-message-ttl of 1800000 (30 minutes)

Also define a "dlq" queue bound to a "dlx" exchange for dead letters.

TASK ROUTING:
Define task_routes as a dict mapping task names to queues:
- "tasks.extract_signals"          → queue "signals"
- "tasks.run_final_analysis"       → queue "analysis"
- "tasks.generate_embeddings"      → queue "embeddings"
- "tasks.scrape_linkedin"          → queue "scraping"
- "tasks.analyze_job_requirements" → queue "analysis"
- "tasks.generate_assessment"      → queue "analysis"
- "tasks.legacy_run_analysis"      → queue "analysis"

CHORD BEHAVIOR:
- task_chord_propagates = False  (chord callback fires even if individual tasks fail)

DEFAULT QUEUE:
- task_default_queue = "signals"

Add a comment block at the top explaining what each section does. Import os at the top.
```

---

### Task 0.4 — Docker Compose Dev Services

**What this does:** Creates `docker-compose.dev.yml` with RabbitMQ and Redis containers for local development. Does not touch the existing `docker-compose.yml` if one exists.

**Cursor Prompt:**

```
Create a new file called docker-compose.dev.yml in the root of the project.

This file is ONLY for local development. It must NOT modify or replace any existing docker-compose.yml.

The file must define exactly these two services:

SERVICE 1 — rabbitmq:
- Image: rabbitmq:3.12-management
- Container name: rabbitmq-local
- Ports: 5672:5672 and 15672:15672
- Environment variables:
  - RABBITMQ_DEFAULT_USER: devuser
  - RABBITMQ_DEFAULT_PASS: devpass
- Volume: rabbitmq_dev_data mounted at /var/lib/rabbitmq
- Restart policy: unless-stopped

SERVICE 2 — redis:
- Image: redis:7.2-alpine
- Container name: redis-local
- Ports: 6379:6379
- Command: redis-server --save 60 1 --loglevel warning
- Volume: redis_dev_data mounted at /data
- Restart policy: unless-stopped

VOLUMES:
- rabbitmq_dev_data: (no options)
- redis_dev_data: (no options)

After creating the file, print it fully so I can verify it before running it.

Do NOT run docker compose. Do NOT modify any Python files. Only create this yml file.
```

---

## PHASE 1 — Redis Layer

---

### Task 1.1 — Redis Client Module

**What this does:** Creates a single shared Redis client that all other modules import. Centralizes connection management so the URL is only read from the environment once.

**Cursor Prompt:**

```
Create a new file at app/services/redis_client.py in my FastAPI leadership assessment project.

This file must:

1. Import redis and os.

2. Read the Redis URL from the environment variable REDIS_URL with default "redis://localhost:6379/0".

3. Create a single module-level Redis client instance called `redis_client` using redis.Redis.from_url() with decode_responses=True.

4. Create a second client instance called `redis_client_bytes` using redis.Redis.from_url() WITHOUT decode_responses=True. This one is used when binary data needs to be stored (Celery internally uses bytes in some operations).

5. Define a function called get_redis() that returns redis_client. This will be used as a FastAPI dependency in routes that need Redis.

6. Define a function called ping_redis() -> bool that calls redis_client.ping() and returns True if successful, False if an exception occurs. This is used in health checks.

7. Add a module-level comment explaining: "Shared Redis client. Import redis_client for all application use. Do NOT create new Redis connections elsewhere."

Do NOT import anything from the rest of the application. This module must have zero dependencies on models, services, or routers.
```

---

### Task 1.2 — Task Registry

**What this does:** Creates `app/services/task_registry.py` — this is the direct replacement for `_PENDING_SIGNAL_TASKS` and `_ANALYSIS_TASKS` in `intelligence.py`. This is one of the most critical correctness fixes in the entire migration.

**Cursor Prompt:**

```
Create a new file at app/services/task_registry.py in my FastAPI leadership assessment project.

This module REPLACES the two in-memory dictionaries currently in app/services/intelligence.py:
  _PENDING_SIGNAL_TASKS: dict[str, list[asyncio.Task]]
  _ANALYSIS_TASKS: dict[str, asyncio.Task]

Those dicts break silently when FastAPI runs with multiple Uvicorn workers because each worker
gets its own private copy. This Redis-backed registry is shared across all workers and all processes.

Import redis_client from app.services.redis_client.
Import logging.
Import json.
Import Optional from typing.

Create a class called TaskRegistry with the following methods:

METHOD 1 — register_signal_task(segment_id: str, candidate_id: str, task_id: str) -> None
  - Stores the task_id in Redis with key "signal_task:{segment_id}", TTL 3600 seconds.
  - Appends task_id to a Redis list at key "signal_tasks_list:{candidate_id}", TTL 3600 seconds.
  - Logs at DEBUG level: "Registered signal task {task_id} for segment {segment_id}"

METHOD 2 — get_signal_task_ids(candidate_id: str) -> list[str]
  - Returns all values from the Redis list at "signal_tasks_list:{candidate_id}".
  - Returns empty list if the key does not exist.

METHOD 3 — acquire_analysis_lock(candidate_id: str, task_id: str) -> bool
  - Attempts to set Redis key "analysis_lock:{candidate_id}" to task_id with NX=True and EX=3600.
  - Returns True if the lock was acquired (key did not exist), False if it already existed.
  - Logs at INFO level when lock is acquired and when it fails.

METHOD 4 — release_analysis_lock(candidate_id: str) -> None
  - Deletes the Redis key "analysis_lock:{candidate_id}".
  - Logs at DEBUG level.

METHOD 5 — is_analysis_running(candidate_id: str) -> bool
  - Returns True if Redis key "analysis_lock:{candidate_id}" exists, False otherwise.

METHOD 6 — set_pipeline_id(candidate_id: str, pipeline_id: str) -> None
  - Stores the Celery chord pipeline ID in Redis at key "pipeline:{candidate_id}", TTL 7200 seconds.

METHOD 7 — get_pipeline_id(candidate_id: str) -> Optional[str]
  - Returns the pipeline ID from Redis, or None if not found.

METHOD 8 — clear_candidate_tasks(candidate_id: str) -> None
  - Deletes "signal_tasks_list:{candidate_id}" and "analysis_lock:{candidate_id}".
  - Used after analysis completes successfully to clean up tracking state.

Create a module-level singleton instance: task_registry = TaskRegistry()

Export both the class and the singleton.
```

---

### Task 1.3 — Read Cache Module

**What this does:** Creates `app/services/cached_reads.py` — wraps the most-called DB reads in Redis caching. This directly reduces PostgreSQL query load when 100 WebSocket connections simultaneously call `get_assessment_definition`, `get_assessment_item_payloads`, and `get_candidate_access_context`.

**Cursor Prompt:**

```
Create a new file at app/services/cached_reads.py in my FastAPI leadership assessment project.

This module wraps existing DB read functions with Redis caching to reduce database load under
concurrent WebSocket connections. Every active interview currently calls get_assessment_definition,
get_assessment_item_payloads, and get_candidate_access_context on WebSocket connect — this module
ensures those identical DB queries only hit Postgres once and then serve from cache.

Import redis_client from app.services.redis_client.
Import json, logging, Optional from typing.
Import asyncio.

Do NOT import the actual DB service functions yet — use TYPE_CHECKING imports or leave them as
forward references with a comment "# These will be wired in Task 3.1". The structure of this
module must be created now; the actual service imports will be connected when the FastAPI
integration tasks are done.

Define the following async functions:

FUNCTION 1 — get_assessment_definition_cached(assessment_id: str, fetch_fn) -> dict
  - Cache key: "cache:assessment:{assessment_id}:definition"
  - TTL: 86400 seconds (24 hours)
  - Try to get from Redis. If hit, return json.loads(value).
  - If miss, call await fetch_fn(assessment_id), store result as JSON in Redis, return it.
  - Log cache hit/miss at DEBUG level.

FUNCTION 2 — get_assessment_items_cached(assessment_id: str, fetch_fn) -> list
  - Cache key: "cache:assessment:{assessment_id}:items"
  - TTL: 86400 seconds (24 hours)
  - Same pattern as above.

FUNCTION 3 — get_candidate_context_cached(candidate_id: str, fetch_fn) -> dict
  - Cache key: "cache:candidate:{candidate_id}:context"
  - TTL: 1800 seconds (30 minutes — shorter because link revocation can change this)
  - Same pattern as above.

FUNCTION 4 — get_job_requirement_profile_cached(job_req_id: str, fetch_fn) -> dict
  - Cache key: "cache:job_req_profile:{job_req_id}"
  - TTL: 21600 seconds (6 hours)
  - Same pattern as above.

INVALIDATION FUNCTIONS (these do NOT fetch, they only delete cache keys):

FUNCTION 5 — invalidate_assessment_cache(assessment_id: str) -> None
  - Deletes: "cache:assessment:{assessment_id}:definition" and "cache:assessment:{assessment_id}:items"
  - Log at INFO level what was invalidated.

FUNCTION 6 — invalidate_candidate_context(candidate_id: str) -> None
  - Deletes: "cache:candidate:{candidate_id}:context"

FUNCTION 7 — invalidate_job_requirement_profile(job_req_id: str) -> None
  - Deletes: "cache:job_req_profile:{job_req_id}"

All Redis operations must be wrapped in try/except. If Redis is unavailable, log a WARNING and
fall back to calling fetch_fn directly (graceful degradation — the app keeps working without cache).
```

---

### Task 1.4 — Rate Limiter Module

**What this does:** Creates a shared Gemini API rate limiter that works across all Celery workers and FastAPI workers. Without this, each process enforces its own limit independently, multiplying your actual API call rate by the number of workers.

**Cursor Prompt:**

```
Create a new file at app/services/rate_limiter.py in my FastAPI leadership assessment project.

This module provides a shared sliding-window rate limiter for the Gemini API. It uses Redis so the
limit is enforced globally across all Celery workers and all FastAPI workers simultaneously.

Import redis_client from app.services.redis_client.
Import os, time, logging.

Define a class called GeminiRateLimiter with:

CONSTRUCTOR:
  - Read GEMINI_RPM_LIMIT from env, default 60 (requests per minute).
  - Read GEMINI_WINDOW_SECONDS from env, default 60.
  - Store both as instance attributes.

METHOD 1 — check_and_increment() -> bool
  - Compute the current window bucket: int(time.time() // self.window_seconds)
  - Redis key: "ratelimit:gemini:{bucket}"
  - INCR the key in Redis (atomic increment).
  - On first increment (result == 1), set TTL to window_seconds * 2 (overlap for window boundaries).
  - Return True if the count after increment is <= self.rpm_limit.
  - Return False if the limit is exceeded.
  - Log at WARNING level when rate limit is hit.

METHOD 2 — wait_for_slot(max_wait_seconds: int = 65) -> bool
  - Polls check_and_increment() in a loop, sleeping 1 second between attempts.
  - Returns True when a slot is available, False if max_wait_seconds is exceeded.
  - This is used by Celery tasks that can afford to wait briefly rather than fail immediately.

METHOD 3 — get_current_usage() -> dict
  - Returns {"current_count": int, "limit": int, "window_seconds": int, "remaining": int}
  - Reads current bucket count from Redis. Returns 0 if key does not exist.

Create a module-level singleton: gemini_rate_limiter = GeminiRateLimiter()

All Redis operations in try/except — if Redis is down, allow the request (fail open) and log WARNING.
```

---

## PHASE 2 — Celery Tasks

---

### Task 2.1 — Sync DB Session for Workers

**What this does:** Creates the synchronous SQLAlchemy session factory for Celery workers. This is required because Celery workers do not have an asyncio event loop, so they cannot use the async SQLAlchemy engine that FastAPI uses. This is the foundation all worker tasks will import from.

**Cursor Prompt:**

```
Create a new file at app/tasks/db.py in my FastAPI leadership assessment project.

This file provides a synchronous SQLAlchemy database session for use inside Celery worker tasks.
Celery workers run in a synchronous Python context and CANNOT use the async SQLAlchemy engine
that FastAPI uses (the one based on asyncpg). Worker tasks need their own separate sync engine.

Import: os, contextlib, sqlalchemy (create_engine), sqlalchemy.orm (sessionmaker, Session).

Do the following:

1. Read the database URL from the environment variable DATABASE_URL.
   - The existing DATABASE_URL likely uses the asyncpg driver format: "postgresql+asyncpg://..."
   - Create a SYNC version by replacing "+asyncpg" with "+psycopg2" in the URL string.
   - If the URL does not contain "+asyncpg", use it as-is.
   - Store the sync URL as SYNC_DATABASE_URL.

2. Create a synchronous SQLAlchemy engine called sync_engine using create_engine(SYNC_DATABASE_URL)
   with pool_pre_ping=True and pool_size=5.

3. Create a session factory called SyncSessionLocal using sessionmaker(bind=sync_engine,
   autocommit=False, autoflush=False).

4. Define a context manager function called get_sync_db() using @contextlib.contextmanager
   that yields a SyncSessionLocal() session and always closes it in the finally block.
   Usage pattern: with get_sync_db() as db: ...

5. Add a comment explaining: "This module is ONLY for Celery workers. FastAPI routes use the
   async session from app/db.py or app/database.py. Never import this in FastAPI route handlers."

Also create the tasks package init file at app/tasks/__init__.py (empty file with a comment
"Celery task modules. Each submodule registers tasks with the celery_app instance.").
```

---

### Task 2.2 — Signal Extraction Task

**What this does:** Creates the Celery task that replaces `asyncio.create_task(extract_signals_for_segment(...))` in the agent WebSocket handler. This is the most frequently dispatched task — one per question per interview.

**Cursor Prompt:**

```
Create a new file at app/tasks/signals.py in my FastAPI leadership assessment project.

This file defines the Celery task for extracting intelligence signals from a response segment.
This task REPLACES the asyncio.create_task(extract_signals_for_segment(...)) call that currently
happens inside the WebSocket handler in app/agent/routes.py.

Context on what it replaces:
- Currently in intelligence.py, after each interview question completes, a response_segment is
  written to the DB, then extract_signals_for_segment() is called as an asyncio task.
- extract_signals_for_segment calls Gemini to analyze the transcript segment and writes
  response_signals rows to the database.
- The task ID is registered in _PENDING_SIGNAL_TASKS so schedule_final_analysis knows to wait.

The new Celery task does the same work but in a worker process, not in the FastAPI event loop.

Imports needed:
- from app.celery_app import celery_app
- from app.tasks.db import get_sync_db
- from app.services.rate_limiter import gemini_rate_limiter
- from app.services.task_registry import task_registry
- import logging, os, asyncio

Define one Celery task:

TASK: extract_signals
- Decorator: @celery_app.task(name="tasks.extract_signals", bind=True, max_retries=3,
  default_retry_delay=15, acks_late=True, queue="signals")
- Parameters: self, segment_id: str, candidate_id: str, assessment_id: str
- Body:
  1. Check Gemini rate limit using gemini_rate_limiter.wait_for_slot(). If it returns False,
     raise self.retry(countdown=30, exc=Exception("Rate limit exceeded")).
  2. Import and call the existing signal extraction logic. Since the existing code is async,
     wrap the async function call with asyncio.run(). Add a comment:
     "# Import the async extraction function from intelligence.py and run it synchronously.
      # The actual function name must match what exists in app/services/intelligence.py.
      # Placeholder: from app.services.intelligence import extract_signals_for_segment
      # asyncio.run(extract_signals_for_segment(segment_id, candidate_id, assessment_id))"
  3. Log at INFO level: "Signal extraction complete for segment {segment_id}"
  4. Return {"segment_id": segment_id, "status": "complete"}
  5. On any exception, call self.retry(exc=exc). On final failure after max_retries, log
     ERROR level with the segment_id and exception.

Add a comment at the top: "Signal extraction task — runs in signal workers, consumes from the
'signals' queue. One task is dispatched per interview question completion."

Important: Leave a TODO comment where the actual import of extract_signals_for_segment goes,
because the exact function signature in app/services/intelligence.py will be wired in Task 3.1.
```

---

### Task 2.3 — Final Analysis Task

**What this does:** Creates the Celery task that replaces `schedule_final_analysis` and `run_full_analysis_chained`. This is the chord callback — it runs after all signal extraction tasks in a group complete.

**Cursor Prompt:**

```
Create a new file at app/tasks/analysis.py in my FastAPI leadership assessment project.

This file defines the Celery task for running the full final analysis pipeline for a completed
interview. This task REPLACES schedule_final_analysis and run_full_analysis_chained from
app/services/intelligence.py.

Context:
- This task is used as the callback in a Celery chord. It receives a list of results from
  all the extract_signals tasks that ran in parallel.
- It acquires a distributed lock so only one analysis runs per candidate, even if rerun is
  triggered while one is already running.
- It writes to analysis, predictions, and assessment_results tables.
- task_chord_propagates is False in the Celery config, so signal_results may contain
  exceptions (from failed signal tasks). The analysis must proceed with whatever data is in DB.

Imports needed:
- from app.celery_app import celery_app
- from app.tasks.db import get_sync_db
- from app.services.task_registry import task_registry
- from app.services.rate_limiter import gemini_rate_limiter
- import logging, asyncio

Define one Celery task:

TASK: run_final_analysis
- Decorator: @celery_app.task(name="tasks.run_final_analysis", bind=True, max_retries=2,
  default_retry_delay=30, acks_late=True, queue="analysis")
- Parameters: self, signal_results: list, candidate_id: str, assessment_id: str
- Body:
  1. Count failed signal results:
     failed_count = sum(1 for r in signal_results if isinstance(r, Exception))
     If failed_count > 0, log WARNING: "{failed_count} signal tasks failed, proceeding with
     partial data for candidate {candidate_id}"
  2. Attempt to acquire the analysis lock:
     acquired = task_registry.acquire_analysis_lock(candidate_id, self.request.id)
     If not acquired: log INFO "Analysis already running for candidate {candidate_id}, skipping"
     and return {"status": "skipped", "reason": "lock_held"}
  3. Try block:
     a. Check Gemini rate limit using gemini_rate_limiter.wait_for_slot().
     b. Import and call the existing full analysis function from intelligence.py using asyncio.run().
        Leave a TODO comment:
        "# from app.services.intelligence import run_full_analysis_chained
         # asyncio.run(run_full_analysis_chained(candidate_id, assessment_id))"
     c. Call task_registry.clear_candidate_tasks(candidate_id) after successful completion.
     d. Log INFO: "Final analysis complete for candidate {candidate_id}"
     e. Return {"status": "completed", "candidate_id": candidate_id}
  4. Except block: release lock with task_registry.release_analysis_lock(candidate_id),
     then raise self.retry(exc=exc).
  5. Finally block: always release lock on any exit path.

Also define a standalone function (NOT a Celery task) called dispatch_analysis_chord:
- Parameters: candidate_id: str, assessment_id: str, segment_ids: list[str]
- Imports celery chord and group
- Builds: chord(group(extract_signals.s(sid, candidate_id, assessment_id) for sid in segment_ids))
  (run_final_analysis.s(candidate_id=candidate_id, assessment_id=assessment_id))
- Stores the pipeline ID in Redis via task_registry.set_pipeline_id()
- Returns the pipeline ID as a string
- Import extract_signals from app.tasks.signals inside this function to avoid circular imports.

This dispatch_analysis_chord function will be called from agent/routes.py in Task 3.2.
```

---

### Task 2.4 — Assessment Creation Task Chain

**What this does:** Creates Celery tasks for the assessment generation pipeline — LinkedIn scraping, job requirement analysis, and assessment record generation. Each becomes one step in a Celery chain, turning a 15–40 second synchronous HTTP request into an immediate 202 response.

**Cursor Prompt:**

```
Create a new file at app/tasks/assessment.py in my FastAPI leadership assessment project.

This file defines Celery tasks for the assessment creation pipeline. Currently, the route
POST /assessments/generate-from-linkedin-job blocks the HTTP connection for 15–40 seconds
while it scrapes LinkedIn, calls Gemini to analyze the job, creates the assessment, and
generates embeddings. This file breaks that into async Celery tasks.

The current synchronous flow in assessment_registry.py / ai_analysis.py / brightdata_linkedin.py is:
  1. scrape LinkedIn job URL (brightdata_linkedin.py)
  2. call Gemini to extract job requirements (ai_analysis.py)
  3. create assessment + assessment_items in DB (assessment_registry.py)
  4. generate context embeddings (rag/embeddings.py)

Each step becomes one task.

Imports:
- from app.celery_app import celery_app
- from app.tasks.db import get_sync_db
- from app.services.rate_limiter import gemini_rate_limiter
- from app.services.cached_reads import invalidate_assessment_cache
- import logging, asyncio, os

Define these four Celery tasks:

TASK 1: scrape_linkedin
- @celery_app.task(name="tasks.scrape_linkedin", bind=True, max_retries=3,
  default_retry_delay=20, acks_late=True, queue="scraping")
- Parameters: self, linkedin_url: str, owner_user_id: str
- Body:
  1. Log INFO: "Scraping LinkedIn URL for user {owner_user_id}"
  2. TODO comment: "# from app.services.brightdata_linkedin import scrape_job_sync or asyncio.run(scrape_job(...))"
  3. Return the raw job data dict.
  4. On exception: self.retry(exc=exc).

TASK 2: analyze_job_requirements
- @celery_app.task(name="tasks.analyze_job_requirements", bind=True, max_retries=2,
  default_retry_delay=15, acks_late=True, queue="analysis")
- Parameters: self, raw_job_data: dict, owner_user_id: str
- Body:
  1. Rate limit check for Gemini.
  2. TODO comment: "# from app.services.ai_analysis import analyze_job_sync or asyncio.run(analyze_job(...))"
  3. Return {"job_requirement_id": str(job_req.id)}
  4. On exception: self.retry(exc=exc).

TASK 3: generate_assessment
- @celery_app.task(name="tasks.generate_assessment", bind=True, max_retries=2,
  default_retry_delay=15, acks_late=True, queue="analysis")
- Parameters: self, job_analysis_result: dict, owner_user_id: str
- Body:
  1. Rate limit check for Gemini.
  2. TODO comment: "# from app.services.assessment_registry import generate_from_job_sync or asyncio.run(...)"
  3. Call invalidate_assessment_cache(assessment_id) after creation to ensure clean cache state.
  4. Return {"assessment_id": str(assessment.id), "job_requirement_id": job_analysis_result["job_requirement_id"]}
  5. On exception: self.retry(exc=exc).

TASK 4: generate_embeddings
- @celery_app.task(name="tasks.generate_embeddings", bind=True, max_retries=3,
  default_retry_delay=10, acks_late=True, queue="embeddings")
- Parameters: self, assessment_result: dict
- Body:
  1. TODO comment: "# from app.rag.embeddings import generate_context_embeddings_sync or asyncio.run(...)"
  2. Log INFO when complete.
  3. Return {"status": "embedded", "assessment_id": assessment_result["assessment_id"]}
  4. On exception: self.retry(exc=exc).

Also define a standalone function called dispatch_linkedin_assessment_chain:
- Parameters: linkedin_url: str, owner_user_id: str
- Builds and dispatches: chain(scrape_linkedin.s(linkedin_url, owner_user_id) | analyze_job_requirements.s(owner_user_id) | generate_assessment.s(owner_user_id) | generate_embeddings.s())
- Returns the task ID as a string.

Also define a standalone function called dispatch_direct_assessment_chain:
- Parameters: owner_user_id: str
- For POST /assessments/generate (no LinkedIn, already has job data)
- Builds: chain(generate_assessment.s(owner_user_id) | generate_embeddings.s())
- Returns task ID.
```

---

### Task 2.5 — Embedding Generation Task

**What this does:** Creates the standalone embedding task for reuse outside the assessment creation chain (e.g., re-embedding after context updates). Also wires the RAG embeddings call properly.

**Cursor Prompt:**

```
In app/tasks/assessment.py (the file created in Task 2.4), add a fifth standalone task for
on-demand embedding regeneration. This is separate from the generate_embeddings task in the
chain because it is also triggered when POST /assessments/context is called to update context.

Add this to the BOTTOM of app/tasks/assessment.py:

TASK 5: regenerate_assessment_embeddings
- @celery_app.task(name="tasks.regenerate_assessment_embeddings", bind=True, max_retries=3,
  default_retry_delay=10, acks_late=True, queue="embeddings")
- Parameters: self, assessment_id: str, reason: str = "manual"
- Body:
  1. Log INFO: "Regenerating embeddings for assessment {assessment_id}, reason: {reason}"
  2. Call invalidate_assessment_cache(assessment_id) first (stale embedding meta should be cleared).
  3. TODO comment for actual rag call: "# from app.rag.embeddings import generate_context_embeddings_sync"
  4. Log INFO when done.
  5. Return {"status": "re-embedded", "assessment_id": assessment_id}
  6. On exception: self.retry(exc=exc).

Also add a helper function called dispatch_embedding_regeneration:
- Parameters: assessment_id: str, reason: str = "manual"
- Dispatches regenerate_assessment_embeddings.apply_async(args=[assessment_id], kwargs={"reason": reason}, queue="embeddings")
- Returns task ID.

Do NOT modify any other part of app/tasks/assessment.py.
```

---

### Task 2.6 — Legacy Analysis Task Wrapper

**What this does:** Wraps the existing `POST /analysis/run` legacy path in a Celery task with the distributed lock. Without this, the legacy path bypasses the Redis lock and can conflict with the modern intelligence pipeline's analysis writes.

**Cursor Prompt:**

```
Create a new file at app/tasks/legacy.py in my FastAPI leadership assessment project.

Context: The codebase has two analysis producers:
1. The modern intelligence.py async pipeline (now being moved to Celery in Task 2.3)
2. The legacy POST /analysis/run path in app/as_analysis/routes/analysis.py

Both write to the analysis and predictions tables. If both run simultaneously for the same
candidate, you get concurrent DB write conflicts and potentially two analysis rows.

This file wraps the legacy path in a Celery task that respects the same Redis distributed lock
as the modern pipeline.

Imports:
- from app.celery_app import celery_app
- from app.tasks.db import get_sync_db
- from app.services.task_registry import task_registry
- import logging, asyncio

Define one Celery task:

TASK: legacy_run_analysis
- @celery_app.task(name="tasks.legacy_run_analysis", bind=True, max_retries=2,
  default_retry_delay=20, acks_late=True, queue="analysis")
- Parameters: self, assessment_id: str, candidate_id: str
- Body:
  1. Try to acquire lock: task_registry.acquire_analysis_lock(candidate_id, self.request.id)
     If not acquired: return {"status": "skipped", "reason": "analysis_already_running"}
  2. Try block:
     Log INFO: "Running legacy analysis for candidate {candidate_id} assessment {assessment_id}"
     TODO comment: "# Import the legacy analysis logic from app/as_analysis/routes/analysis.py
     # or its underlying service. Use asyncio.run() to call async functions.
     # The actual function to call is the core logic of the /analysis/run endpoint."
     Return {"status": "complete", "candidate_id": candidate_id}
  3. Except: release lock, self.retry(exc=exc)
  4. Finally: always release lock.

Also define a helper function dispatch_legacy_analysis:
- Parameters: assessment_id: str, candidate_id: str
- Checks lock first: if task_registry.is_analysis_running(candidate_id), return {"status": "already_running"}
- Dispatches legacy_run_analysis.apply_async(args=[assessment_id, candidate_id], queue="analysis")
- Returns task ID.
```

---

## PHASE 3 — FastAPI Integration

---

### Task 3.1 — Modify intelligence.py

**What this does:** Removes `_PENDING_SIGNAL_TASKS` and `_ANALYSIS_TASKS` from `intelligence.py` and replaces them with calls to `task_registry` and the Celery dispatch functions. This is the most delicate modification — existing functions must be preserved as async (for any callers not yet migrated) but the in-memory tracking is removed.

**Cursor Prompt:**

```
Modify app/services/intelligence.py in my FastAPI leadership assessment project.

This is one of the most critical changes in the entire migration. Read these instructions carefully
and do not skip any step.

WHAT EXISTS NOW in intelligence.py:
- Two module-level dicts: _PENDING_SIGNAL_TASKS and _ANALYSIS_TASKS
- A function register_signal_task() that appends asyncio.Task objects to _PENDING_SIGNAL_TASKS
- A function schedule_final_analysis() that calls asyncio.gather() on pending tasks then runs analysis

WHAT MUST CHANGE:

STEP 1 — Remove the in-memory dicts:
Delete or comment out (with a clear deprecation comment) the following:
  _PENDING_SIGNAL_TASKS: dict[str, list[asyncio.Task]] = {}
  _ANALYSIS_TASKS: dict[str, asyncio.Task] = {}

Add a comment where they were:
  # REMOVED: In-memory task tracking replaced by Redis-backed TaskRegistry.
  # See app/services/task_registry.py. These dicts broke silently under multiple Uvicorn workers.

STEP 2 — Replace register_signal_task():
The existing register_signal_task() function that appended asyncio.Task objects must be changed
to instead call task_registry.register_signal_task(segment_id, candidate_id, task_id).
The function signature stays the same but now takes a task_id: str instead of an asyncio.Task.
Import task_registry from app.services.task_registry.

STEP 3 — Replace schedule_final_analysis():
The existing schedule_final_analysis() function that called asyncio.gather() must be changed
to instead call dispatch_analysis_chord() from app.tasks.analysis.
Import dispatch_analysis_chord from app.tasks.analysis.
The function must:
  1. Fetch segment_ids from the database (existing DB query logic stays).
  2. Call dispatch_analysis_chord(candidate_id, assessment_id, segment_ids).
  3. Return the pipeline ID.
  4. NOT await anything related to the analysis itself — it is now fire-and-forget.

STEP 4 — Wire the TODO in tasks/signals.py:
Now that you are inside intelligence.py, find the function extract_signals_for_segment (or whatever
it is named). Make sure it can be called synchronously. If it is async, create a sync wrapper:

def extract_signals_for_segment_sync(segment_id: str, candidate_id: str, assessment_id: str):
    import asyncio
    return asyncio.run(extract_signals_for_segment(segment_id, candidate_id, assessment_id))

Then go to app/tasks/signals.py and replace the TODO comment with:
    from app.services.intelligence import extract_signals_for_segment_sync
    extract_signals_for_segment_sync(segment_id, candidate_id, assessment_id)

STEP 5 — Wire the TODO in tasks/analysis.py:
Find run_full_analysis_chained (or equivalent) in intelligence.py. Create a sync wrapper if async:

def run_full_analysis_chained_sync(candidate_id: str, assessment_id: str):
    import asyncio
    return asyncio.run(run_full_analysis_chained(candidate_id, assessment_id))

Then go to app/tasks/analysis.py and replace the TODO comment with:
    from app.services.intelligence import run_full_analysis_chained_sync
    run_full_analysis_chained_sync(candidate_id, assessment_id)

STEP 6 — Do NOT remove any other functions from intelligence.py.
The ensure_job_requirement_profile function, signal extraction logic, and analysis chaining
logic must all remain intact. Only the in-memory tracking and asyncio.gather() orchestration
is replaced.

After all changes, print the full diff of intelligence.py so I can review it.
```

---

### Task 3.2 — Modify agent/routes.py

**What this does:** Modifies the WebSocket handler to dispatch Celery tasks instead of creating asyncio tasks. The WebSocket handler becomes purely a real-time communication bridge to Gemini — all intelligence processing is offloaded.

**Cursor Prompt:**

```
Modify app/agent/routes.py in my FastAPI leadership assessment project.

Context: The WebSocket handler at /agent/ws currently does three things that will be changed:
1. After each question completes, it calls asyncio.create_task(extract_signals_for_segment(...))
   and registers that task in _PENDING_SIGNAL_TASKS.
2. When the interview ends, it calls schedule_final_analysis() which awaits all pending signals
   then runs the full Gemini analysis pipeline — blocking the WebSocket for potentially minutes.
3. It directly coordinates the pipeline timing through asyncio.

WHAT MUST CHANGE:

STEP 1 — Replace extract_signals_for_segment dispatch:
Find every place in agent/routes.py where extract_signals_for_segment is called as an asyncio task.
Replace with:
  from app.tasks.signals import extract_signals
  from app.services.task_registry import task_registry
  
  task = extract_signals.apply_async(
      args=[segment_id, candidate_id, assessment_id],
      queue="signals",
  )
  task_registry.register_signal_task(segment_id, candidate_id, task.id)
  logger.info(f"Signal extraction dispatched: task_id={task.id} segment={segment_id}")

Do NOT await anything. This must be fire-and-forget.

STEP 2 — Replace schedule_final_analysis dispatch:
Find where schedule_final_analysis() is called (at interview end, on disconnect, or when agent
advances past last question). Replace with:
  from app.tasks.analysis import dispatch_analysis_chord
  
  pipeline_id = dispatch_analysis_chord(candidate_id, assessment_id, segment_ids)
  logger.info(f"Analysis pipeline dispatched: pipeline_id={pipeline_id} candidate={candidate_id}")
  # WebSocket can now close immediately. Analysis runs in Celery workers.

The segment_ids list should come from the segments written during the interview. If this list
is not already tracked in the WebSocket session state, add a list to the session context that
appends each segment_id when write_segment is called.

STEP 3 — Replace register_signal_task calls:
If register_signal_task from intelligence.py is called anywhere in agent/routes.py, replace it
with task_registry.register_signal_task(segment_id, candidate_id, task.id) from Task 3.1's
updated signature.

STEP 4 — Replace get_assessment_definition and get_assessment_item_payloads calls:
At WebSocket connection setup, find the calls to:
- get_assessment_definition(assessment_id)
- get_assessment_item_payloads(assessment_id)
- get_candidate_access_context (or equivalent)

Wrap each with the cached versions from app.services.cached_reads:
  from app.services.cached_reads import (
      get_assessment_definition_cached,
      get_assessment_items_cached,
      get_candidate_context_cached,
  )
  
  definition = await get_assessment_definition_cached(
      assessment_id,
      fetch_fn=lambda aid: existing_function(aid, db)  # keep existing fetch function
  )

Do NOT change the autosave loop (save_assessment_answer every 5 seconds). Leave it exactly as is.
Do NOT change the Gemini Live proxying logic. Leave it exactly as is.
Do NOT change the transcript handling logic. Leave it exactly as is.

Only change: signal extraction dispatch, final analysis dispatch, and WebSocket setup caching.

Print the diff of all changed sections after modification.
```

---

### Task 3.3 — Modify assessment_registry.py

**What this does:** Converts the assessment creation HTTP endpoints from synchronous blocking requests to immediate 202 responses with Celery task dispatch.

**Cursor Prompt:**

```
Modify the assessment creation routes in my FastAPI leadership assessment project.

The routes to modify are in app/as_blueprinting/routes/assessment.py (and/or wherever
POST /assessments/generate and POST /assessments/generate-from-linkedin-job are defined).

Context: These routes currently block the HTTP connection for 15–40 seconds while they:
1. Scrape LinkedIn (brightdata_linkedin.py)
2. Call Gemini for job analysis (ai_analysis.py)
3. Create assessment in DB (assessment_registry.py)
4. Generate embeddings (rag/embeddings.py)

WHAT MUST CHANGE:

ROUTE 1 — POST /assessments/generate-from-linkedin-job:
Change the response from 200 to 202 Accepted.
Replace the blocking service calls with:
  from app.tasks.assessment import dispatch_linkedin_assessment_chain
  
  task_id = dispatch_linkedin_assessment_chain(
      linkedin_url=payload.linkedin_url,
      owner_user_id=str(current_user.id),
  )
  return JSONResponse(
      status_code=202,
      content={"status": "accepted", "task_id": task_id,
               "poll_url": f"/assessments/task-status/{task_id}"}
  )

ROUTE 2 — POST /assessments/generate:
If this route does NOT involve LinkedIn scraping (direct job data provided), change to 202 and:
  from app.tasks.assessment import dispatch_direct_assessment_chain
  task_id = dispatch_direct_assessment_chain(owner_user_id=str(current_user.id))
  return JSONResponse(status_code=202, content={"status": "accepted", "task_id": task_id})

ADD NEW ROUTE — GET /assessments/task-status/{task_id}:
Add a new read-only endpoint for polling assessment creation progress:
  from celery.result import AsyncResult
  
  @router.get("/assessments/task-status/{task_id}")
  async def get_assessment_task_status(task_id: str, current_user: User = Depends(...)):
      result = AsyncResult(task_id)
      return {
          "task_id": task_id,
          "state": result.state,
          "ready": result.ready(),
          "successful": result.successful() if result.ready() else None,
          "result": result.result if result.successful() else None,
      }

IMPORTANT: Keep ALL existing validation, authentication, and request parsing exactly as is.
Only change the part where service functions are called and the response is returned.
Do NOT remove or change any Depends() decorators, request body models, or auth checks.

Print the diff of changes.
```

---

### Task 3.4 — Modify routers/intelligence.py

**What this does:** Removes the GET-triggers-compute anti-pattern from the status endpoint and wires the rerun endpoint to dispatch Celery chords instead of calling analysis functions directly.

**Cursor Prompt:**

```
Modify app/routers/intelligence.py in my FastAPI leadership assessment project.

Context — Two critical problems exist in this file:

PROBLEM 1 — GET /intelligence/assessment/{id}/status can trigger schedule_final_analysis().
This is dangerous because: admins polling this endpoint can trigger multiple simultaneous
Gemini analysis runs. Under concurrent polling, multiple analysis rows could be written.
This endpoint must become read-only. It must NEVER trigger computation.

PROBLEM 2 — POST /intelligence/assessment/{id}/rerun calls analysis functions directly in-process,
bypassing the distributed Redis lock. Two concurrent reruns can race and write duplicate analysis.

WHAT MUST CHANGE:

CHANGE 1 — GET /intelligence/assessment/{id}/status:
Find the existing handler. It likely calls schedule_final_analysis() or similar when analysis is missing.
Replace the compute-triggering logic with pure state reads:

  from celery.result import AsyncResult
  from app.services.task_registry import task_registry
  from app.services.redis_client import redis_client
  
  # Check Celery pipeline state
  pipeline_id = task_registry.get_pipeline_id(candidate_id)
  if pipeline_id:
      result = AsyncResult(pipeline_id)
      if result.state == "SUCCESS":
          return {"status": "complete", "source": "pipeline"}
      elif result.state == "FAILURE":
          return {"status": "failed", "source": "pipeline", "error": str(result.result)}
      else:
          return {"status": "processing", "state": result.state, "source": "pipeline"}
  
  # Check DB for completed analysis
  analysis = await get_analysis_by_candidate(candidate_id, db)  # use existing DB fetch
  if analysis:
      return {"status": "complete", "source": "database"}
  
  # Nothing running, nothing in DB
  return {"status": "pending", "message": "Use POST /rerun to trigger analysis"}

DO NOT call schedule_final_analysis() from this endpoint under any circumstances.
Add a comment: "# READ-ONLY. This endpoint never triggers computation. See POST /rerun."

CHANGE 2 — POST /intelligence/assessment/{id}/rerun:
Replace the direct service call with a Celery chord dispatch:

  from app.tasks.analysis import dispatch_analysis_chord
  from app.services.task_registry import task_registry
  
  # Check if already running
  if task_registry.is_analysis_running(candidate_id):
      return {"status": "already_running", "message": "Analysis is already in progress"}
  
  # Fetch existing segment IDs from DB
  segment_ids = await get_segment_ids_for_candidate(candidate_id, db)
  if not segment_ids:
      return {"status": "error", "message": "No segments found. Interview may be incomplete."}
  
  pipeline_id = dispatch_analysis_chord(candidate_id, assessment_id, segment_ids)
  return {"status": "dispatched", "pipeline_id": pipeline_id}

Keep all existing authentication, query parameters, and route decorators exactly as is.
Keep all existing DB read calls (for actual analysis results) exactly as is.
Only change the logic that triggers or checks computation.

Print the diff.
```

---

### Task 3.5 — Modify routers/candidate.py

**What this does:** Wraps read calls in candidate-facing routes with Redis caching where the same data is repeatedly fetched across interview sessions.

**Cursor Prompt:**

```
Modify app/routers/candidate.py in my FastAPI leadership assessment project.

Context: Candidate-facing read endpoints (GET /candidate/assessment/{id}/analysis,
GET /candidate/assessment/{id}/predictions, GET /candidate/assessment/{id}/status) are
called frequently during and after interviews. Some of these hit DB tables that do not
change during an active interview session.

WHAT MUST CHANGE:

CHANGE 1 — In GET /candidate/interview/{token} (candidate registration context):
Find the call that fetches candidate registration context (likely assessment_candidates.get_candidate_registration_context or similar).
Wrap it with get_candidate_context_cached from app.services.cached_reads:

  from app.services.cached_reads import get_candidate_context_cached
  
  context = await get_candidate_context_cached(
      candidate_id=candidate_id,
      fetch_fn=lambda cid: existing_fetch_function(cid, db),
  )

CHANGE 2 — Add ETag-style short-circuit for result endpoints:
For GET /candidate/assessment/{id}/analysis and GET /candidate/assessment/{id}/predictions:
Before hitting the DB, check if the analysis pipeline is still running:

  from app.services.task_registry import task_registry
  
  if task_registry.is_analysis_running(candidate_id):
      return {"status": "processing", "message": "Analysis is still being generated"}

This prevents DB queries for data that is not ready yet, returning immediately.

CHANGE 3 — In GET /candidate/interview/{token}/begin (candidate registration):
After registration completes successfully, invalidate the candidate context cache because
the access link state has changed:

  from app.services.cached_reads import invalidate_candidate_context
  invalidate_candidate_context(candidate_id)

IMPORTANT: Do NOT change any authentication logic, token validation, or DB write logic.
Do NOT change the POST /candidate/interview/{token}/begin registration writes.
Only add caching wrappers and the analysis-running guard.

Print the diff.
```

---

## PHASE 4 — Cache Invalidation

---

### Task 4.1 — Invalidation Hooks

**What this does:** Adds cache invalidation calls at every point where cached data can become stale. Without this, cached assessment definitions could serve old data after an update.

**Cursor Prompt:**

```
Add cache invalidation calls at all data mutation points in my FastAPI leadership assessment project.
This task must touch multiple files. For each, only add the invalidation call — do not change
any other logic.

Import from app.services.cached_reads: invalidate_assessment_cache, invalidate_candidate_context,
invalidate_job_requirement_profile wherever needed.

FILE 1 — app/services/interview_links.py:
In the function that handles POST /assessments/{id}/invite-link/revoke (link revocation):
After the DB update that revokes the link, add:
  await asyncio.get_event_loop().run_in_executor(None, invalidate_candidate_context, candidate_id)
  # or if this function is already sync: invalidate_candidate_context(candidate_id)
Add comment: "# Revocation changes access state — invalidate candidate context cache"

FILE 2 — app/services/assessment_registry.py:
In the function that creates or updates an assessment record:
After the DB commit, add: invalidate_assessment_cache(str(assessment.id))
Add comment: "# Assessment updated — invalidate definition and items cache"

FILE 3 — app/as_blueprinting/routes/assessment.py:
In POST /assessments/context (if this endpoint updates context embeddings or assessment text):
After the update, dispatch: dispatch_embedding_regeneration(assessment_id, reason="context_update")
And call: invalidate_assessment_cache(assessment_id)
Import dispatch_embedding_regeneration from app.tasks.assessment.

FILE 4 — app/tasks/analysis.py:
In run_final_analysis task, after successful completion:
Call invalidate_job_requirement_profile(job_req_id) if job_req_id is available in context.
Add comment: "# Profile may have been rebuilt during analysis — invalidate profile cache"

For each file, print only the lines that changed plus 3 lines of context above and below.
Do NOT reprint entire files.
```

---

## PHASE 5 — GCP Deployment

---

### Task 5.1 — Dockerfile for Celery Workers

**What this does:** Creates a dedicated Dockerfile for Celery worker containers, separate from the FastAPI application container. Workers need the same codebase but a different entrypoint command.

**Cursor Prompt:**

```
Create a new file called Dockerfile.worker in the root of my FastAPI leadership assessment project.

This Dockerfile is for Celery worker containers only. It should be based on the same base image
as the existing Dockerfile (check the existing Dockerfile first and use the same Python version).

The Dockerfile.worker must:

1. Use the same base image as the existing Dockerfile (e.g., python:3.11-slim).

2. Set WORKDIR to /app.

3. Copy requirements.txt and run pip install --no-cache-dir -r requirements.txt.

4. Copy the entire application code.

5. NOT expose any ports (workers do not serve HTTP).

6. Set the default CMD to:
   celery -A app.celery_app worker --loglevel=info
   
   This is overridden per worker type in docker-compose and GCP deployments.

7. Add a build ARG called WORKER_QUEUE with default value "signals".
   Use it in the CMD so different queues can be built into different images if needed:
   CMD ["sh", "-c", "celery -A app.celery_app worker -Q ${WORKER_QUEUE} --loglevel=info"]

8. Set these ENV defaults (overridden at runtime):
   ENV CELERY_BROKER_URL=amqp://devuser:devpass@rabbitmq:5672//
   ENV CELERY_RESULT_BACKEND=redis://redis:6379/0
   ENV REDIS_URL=redis://redis:6379/0

After creating the file, check the existing Dockerfile and confirm the base image and Python
version match. If they differ, use the existing Dockerfile's base image.

Print the final Dockerfile.worker.
```

---

### Task 5.2 — GCP Deployment Config

**What this does:** Creates the GCP deployment configuration for running Celery workers as a persistent service on GCE, connected to Memorystore and CloudAMQP.

**Cursor Prompt:**

```
Create two new files for GCP deployment of Celery workers in my FastAPI leadership assessment project.

FILE 1 — Create deploy/workers/startup-signals.sh:
A shell script for starting the signals worker on a GCE VM.

Content:
#!/bin/bash
set -e

# Pull latest image from Google Artifact Registry
docker pull REGION-docker.pkg.dev/PROJECT_ID/REPO/leadership-worker:latest

# Stop existing container if running
docker stop celery-signals 2>/dev/null || true
docker rm celery-signals 2>/dev/null || true

# Start signals worker
docker run -d \
  --name celery-signals \
  --restart unless-stopped \
  -e CELERY_BROKER_URL="${CELERY_BROKER_URL}" \
  -e CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND}" \
  -e REDIS_URL="${REDIS_URL}" \
  -e DATABASE_URL="${DATABASE_URL}" \
  -e GEMINI_RPM_LIMIT="${GEMINI_RPM_LIMIT:-60}" \
  REGION-docker.pkg.dev/PROJECT_ID/REPO/leadership-worker:latest \
  celery -A app.celery_app worker -Q signals -c 8 --loglevel=info

Add comments explaining: replace REGION, PROJECT_ID, REPO with actual values.
Add a note: env vars should be loaded from a GCP Secret Manager sourced script before running this.

FILE 2 — Create deploy/workers/startup-analysis.sh:
Same structure but for the analysis worker:
  celery -A app.celery_app worker -Q analysis -c 4 --loglevel=info
Container name: celery-analysis

FILE 3 — Create deploy/workers/startup-background.sh:
Same structure but for the background worker:
  celery -A app.celery_app worker -Q embeddings,scraping -c 2 --loglevel=info
Container name: celery-background

FILE 4 — Create deploy/workers/README.md:
A short guide explaining:
1. Prerequisites: GCE VM with Docker installed, access to Artifact Registry, env vars from Secret Manager.
2. How to build and push the worker image: docker build -f Dockerfile.worker -t ... && docker push ...
3. How to SSH into the VM and run each startup script.
4. How to check worker logs: docker logs -f celery-signals
5. How to verify workers are connected: docker exec celery-signals celery -A app.celery_app inspect ping

Create all four files. Do NOT modify any Python files in this task.
```

---

## PHASE 6 — Observability

---

### Task 6.1 — Flower Monitoring

**What this does:** Adds Flower to the development Docker Compose and creates a startup script for production.

**Cursor Prompt:**

```
Add Flower monitoring to my FastAPI leadership assessment project.

CHANGE 1 — Add Flower to docker-compose.dev.yml:
Open the existing docker-compose.dev.yml (created in Task 0.4) and add a third service:

  flower:
    image: mher/flower:2.0
    container_name: flower-local
    ports:
      - "5555:5555"
    environment:
      CELERY_BROKER_URL: amqp://devuser:devpass@rabbitmq:5672//
      CELERY_RESULT_BACKEND: redis://redis:6379/0
    command: celery flower --broker=amqp://devuser:devpass@rabbitmq:5672// --port=5555
    depends_on:
      - rabbitmq
      - redis
    restart: unless-stopped

CHANGE 2 — Create app/routers/health.py:
A FastAPI health check router that verifies all three services are reachable.

  from fastapi import APIRouter
  from app.services.redis_client import ping_redis
  
  router = APIRouter(prefix="/health", tags=["health"])
  
  @router.get("/")
  async def health_check():
      redis_ok = ping_redis()
      # Celery worker check via inspect (non-blocking, 1s timeout)
      try:
          from app.celery_app import celery_app
          inspector = celery_app.control.inspect(timeout=1.0)
          active = inspector.active()
          celery_ok = active is not None
      except Exception:
          celery_ok = False
      
      return {
          "status": "ok" if (redis_ok and celery_ok) else "degraded",
          "redis": "ok" if redis_ok else "unreachable",
          "celery_workers": "ok" if celery_ok else "no workers found",
      }

CHANGE 3 — Register the health router in main.py (or wherever FastAPI app is created):
  from app.routers.health import router as health_router
  app.include_router(health_router)

Print diffs for all changed files.
```

---

### Task 6.2 — DLQ Alert Handler

**What this does:** Creates a Celery beat scheduled task that monitors the dead letter queue and logs alerts when failed tasks accumulate.

**Cursor Prompt:**

```
Create a new file at app/tasks/monitoring.py in my FastAPI leadership assessment project.

This file defines a Celery beat periodic task that monitors the dead letter queue (DLQ) in
RabbitMQ and logs warnings when failed tasks accumulate. This is the alerting layer for the
analysis pipeline — if signal extraction or final analysis is failing at scale, this catches it.

Imports:
- from app.celery_app import celery_app
- import logging, os
- from kombu import Connection

Define one Celery periodic task:

TASK: check_dlq_depth
- @celery_app.task(name="tasks.check_dlq_depth")
- Parameters: none
- Body:
  1. Connect to RabbitMQ using the CELERY_BROKER_URL env var.
  2. Check the message count in the "dlq" queue using kombu Connection and SimpleQueue.
  3. If count > 0: log WARNING with the count and a message:
     "ALERT: {count} failed tasks in dead letter queue. Check Flower for details."
  4. If count > 10: log ERROR: "CRITICAL: DLQ has {count} messages. Analysis pipeline may be failing."
  5. Return {"dlq_depth": count}
  6. Wrap everything in try/except — if broker is unreachable, log WARNING and return gracefully.

Also add the beat schedule to celery_config.py (open that file and add at the bottom):
  from celery.schedules import crontab
  
  beat_schedule = {
      "check-dlq-every-5-minutes": {
          "task": "tasks.check_dlq_depth",
          "schedule": 300.0,  # every 5 minutes
      },
  }

Add a note in the file: "To run the beat scheduler alongside workers:
celery -A app.celery_app beat --loglevel=info
Or combined: celery -A app.celery_app worker --beat --loglevel=info (dev only)"

Print both files after changes.
```

---

## PHASE 7 — Testing

---

### Task 7.1 — Unit Tests for Celery Tasks

**What this does:** Creates unit tests for the Celery tasks using Celery's `CELERY_TASK_ALWAYS_EAGER` mode, which runs tasks synchronously in the test process without needing a broker.

**Cursor Prompt:**

```
Create a new file at tests/test_celery_tasks.py in my FastAPI leadership assessment project.

This file contains unit tests for the Celery tasks. Tests use CELERY_TASK_ALWAYS_EAGER=True which
runs tasks synchronously without needing RabbitMQ or real Celery workers running.

Use pytest and unittest.mock. Do NOT use real DB connections — mock the DB calls.
Do NOT connect to real Redis — use fakeredis or mock the redis_client.

Setup at top of file:
  import pytest
  from unittest.mock import patch, MagicMock
  from celery.contrib.pytest import celery_app, celery_config
  
  @pytest.fixture(scope="module")
  def celery_config():
      return {
          "task_always_eager": True,
          "task_eager_propagates": True,
          "broker_url": "memory://",
          "result_backend": "cache+memory://",
      }

Write these tests:

TEST 1 — test_extract_signals_task_success:
  - Mock asyncio.run to return None (simulating successful signal extraction).
  - Mock gemini_rate_limiter.wait_for_slot to return True.
  - Call extract_signals.apply(args=["seg_1", "cand_1", "assess_1"]).
  - Assert result.status == "SUCCESS" and result.result["segment_id"] == "seg_1".

TEST 2 — test_extract_signals_retries_on_failure:
  - Mock asyncio.run to raise Exception("Gemini timeout").
  - Mock gemini_rate_limiter.wait_for_slot to return True.
  - Assert the task raises the exception (since eager propagation is on).

TEST 3 — test_run_final_analysis_acquires_lock:
  - Mock task_registry.acquire_analysis_lock to return True.
  - Mock task_registry.release_analysis_lock.
  - Mock asyncio.run to return None.
  - Mock gemini_rate_limiter.wait_for_slot to return True.
  - Call run_final_analysis.apply(args=[[{"status": "complete"}], "cand_1", "assess_1"]).
  - Assert acquire_analysis_lock was called with "cand_1".
  - Assert release_analysis_lock was called in finally.

TEST 4 — test_run_final_analysis_skips_if_lock_held:
  - Mock task_registry.acquire_analysis_lock to return False (lock already held).
  - Call run_final_analysis.apply(args=[[], "cand_1", "assess_1"]).
  - Assert result is {"status": "skipped", "reason": "lock_held"}.
  - Assert asyncio.run was NOT called.

TEST 5 — test_task_registry_cross_process_isolation:
  - Import task_registry from app.services.task_registry.
  - Mock redis_client.set and redis_client.rpush.
  - Call task_registry.register_signal_task("seg_1", "cand_1", "task_abc").
  - Assert redis_client.set was called with key "signal_task:seg_1".
  - Assert redis_client.rpush was called with key "signal_tasks_list:cand_1".
  This test proves the registry uses Redis (shared state) not in-memory dicts.

TEST 6 — test_rate_limiter_shared_across_calls:
  - Mock redis_client.incr to return 61 (over limit).
  - Call gemini_rate_limiter.check_and_increment().
  - Assert it returns False.
  - Mock redis_client.incr to return 30 (under limit).
  - Assert it returns True.

Print the full test file.
```

---

### Task 7.2 — Integration Test: Full Interview Pipeline

**What this does:** Creates an end-to-end integration test that simulates a full interview flow from WebSocket connection through signal extraction to final analysis, verifying the entire Celery pipeline works together.

**Cursor Prompt:**

```
Create a new file at tests/test_interview_pipeline_integration.py in my FastAPI leadership
assessment project.

This is an end-to-end integration test. It requires RabbitMQ and Redis to be running locally
(from docker-compose.dev.yml). It does NOT require a live Gemini connection — mock Gemini calls.
It uses a real test DB or SQLite for DB operations.

Mark all tests in this file with: @pytest.mark.integration

This test simulates the full flow:
  Candidate begins interview → Questions answered → Signals extracted → Final analysis runs

Write these tests:

TEST 1 — test_signal_task_writes_to_redis_registry:
  Description: After dispatching an extract_signals task, the task ID should appear in Redis.
  Steps:
    1. Call task_registry.register_signal_task("seg_test", "cand_test", "task_test_id")
    2. result = task_registry.get_signal_task_ids("cand_test")
    3. Assert "task_test_id" in result
    4. Cleanup: redis_client.delete("signal_tasks_list:cand_test")

TEST 2 — test_analysis_lock_prevents_duplicate_runs:
  Description: Two simultaneous rerun requests should result in only one analysis running.
  Steps:
    1. First call: acquired1 = task_registry.acquire_analysis_lock("cand_dup", "task_001")
    2. Second call: acquired2 = task_registry.acquire_analysis_lock("cand_dup", "task_002")
    3. Assert acquired1 == True
    4. Assert acquired2 == False
    5. Cleanup: task_registry.release_analysis_lock("cand_dup")

TEST 3 — test_full_pipeline_chord_dispatch:
  Description: dispatch_analysis_chord creates a valid Celery chord and stores pipeline ID in Redis.
  Steps:
    1. Mock extract_signals.apply_async and run_final_analysis.s.
    2. Call dispatch_analysis_chord("cand_chord_test", "assess_test", ["seg_a", "seg_b", "seg_c"])
    3. Assert task_registry.get_pipeline_id("cand_chord_test") is not None
    4. Assert the chord was constructed with 3 signal tasks (one per segment).
    5. Cleanup: redis_client.delete("pipeline:cand_chord_test")

TEST 4 — test_status_endpoint_never_triggers_compute:
  Description: GET /intelligence/assessment/{id}/status must never call dispatch functions.
  Steps:
    1. Use FastAPI TestClient.
    2. Mock get_analysis_by_candidate to return None (no analysis yet).
    3. Mock task_registry.get_pipeline_id to return None.
    4. GET /intelligence/assessment/test_assess/status?candidate_id=test_cand
    5. Assert response status is 200.
    6. Assert response body contains "status": "pending".
    7. Assert dispatch_analysis_chord was NOT called (patch it and check call count == 0).

TEST 5 — test_assessment_creation_returns_202:
  Description: POST /assessments/generate-from-linkedin-job should return 202 immediately.
  Steps:
    1. Use FastAPI TestClient with auth mocked.
    2. Mock dispatch_linkedin_assessment_chain to return "fake_task_id".
    3. POST /assessments/generate-from-linkedin-job with a dummy payload.
    4. Assert response.status_code == 202.
    5. Assert response.json()["task_id"] == "fake_task_id".
    6. Assert the response returned in under 500ms (time the call).

Add a conftest.py fixture in tests/ if it does not exist that starts the test FastAPI app
using httpx AsyncClient or FastAPI TestClient.

Print the full test file.
```

---

## Final Checklist Before Going Live

After all 7 phases are complete, verify every item on this list before routing production traffic through the new system:

```
INFRASTRUCTURE
  [ ] RabbitMQ running on GCP, management UI accessible
  [ ] Redis (Memorystore) running, connection verified from worker VM
  [ ] All three Celery worker services running (signals, analysis, background)
  [ ] Flower accessible and showing all workers as online
  [ ] DLQ queue exists in RabbitMQ (verify in management UI)

CODE CORRECTNESS
  [ ] _PENDING_SIGNAL_TASKS removed from intelligence.py
  [ ] _ANALYSIS_TASKS removed from intelligence.py
  [ ] GET /intelligence/status never calls dispatch functions
  [ ] WebSocket handler dispatches Celery tasks, does not await analysis
  [ ] Assessment creation routes return 202
  [ ] Legacy /analysis/run goes through distributed lock

CACHE
  [ ] Assessment definition cache works (run same request twice, second is Redis hit)
  [ ] Candidate context invalidated on link revocation
  [ ] Assessment cache invalidated on update

TESTS
  [ ] All unit tests pass (pytest tests/test_celery_tasks.py)
  [ ] All integration tests pass (pytest -m integration)
  [ ] Health endpoint returns ok for redis and celery_workers

LOAD TEST (optional but recommended)
  [ ] Simulate 10 concurrent WebSocket connections
  [ ] Verify 10 analysis pipelines complete without cross-contamination
  [ ] Verify no duplicate analysis rows in DB after all pipelines finish
```

---

*This document is the implementation companion to `scalability_analysis.md`.
All task prompts are written against the FastAPI + PostgreSQL + Gemini Live stack
and the specific module structure described in `application_visualization.md`.*