# Scalability Analysis: Celery, RabbitMQ & Redis for the Leadership Assessment Platform

> **Scope:** A deep-dive analysis of the current FastAPI + PostgreSQL + Gemini Live architecture against the proposed addition of Celery, RabbitMQ, and Redis. This document identifies every failure point under concurrent load, maps each technology to the exact process it solves, and provides implementation guidance tied to the actual codebase modules described in `application_visualization.md`.

---

## Table of Contents

1. [Current Architecture Diagnosis — Where It Breaks at 100 Users](#1-current-architecture-diagnosis)
2. [The Full Technology Stack Map](#2-the-full-technology-stack-map)
3. [RabbitMQ — Where It Goes and Why](#3-rabbitmq)
4. [Celery — Where It Goes and Why](#4-celery)
5. [Redis — Where It Goes and Why](#5-redis)
6. [Implementation Guide Against Your Current Codebase](#6-implementation-guide)
7. [Migration Details You Must Know](#7-migration-details)
8. [Updated Architecture Diagrams](#8-updated-architecture-diagrams)
9. [Verdict: Is Any of Them Unnecessary?](#9-verdict)

---

## 1. Current Architecture Diagnosis

Before prescribing solutions, it is critical to understand exactly what breaks and at what point it breaks. This section traces the actual failure modes in your current system under concurrent load.

### 1.1 The In-Process Queue Problem — Your Biggest Risk

Located in `intelligence.py`, your current concurrency model relies on two in-memory Python dictionaries:

```
_PENDING_SIGNAL_TASKS  →  tracks asyncio.Task objects for signal extraction
_ANALYSIS_TASKS        →  tracks whether a final analysis task is running per scope
```

**Why this silently destroys correctness at scale:**

When you deploy FastAPI with multiple Uvicorn workers (which you must do to handle 100 concurrent users), each OS process gets its own copy of these dictionaries. Worker 1 dispatches a signal extraction task for Candidate A and registers it in its own `_PENDING_SIGNAL_TASKS`. Worker 2 — which may handle the next WebSocket message from the same Candidate A — has no idea that task exists. When `schedule_final_analysis` runs, it waits for tasks that only Worker 1 knows about, potentially firing final analysis before all signals are collected, or never firing it at all.

```
Worker 1 (PID 1001)                Worker 2 (PID 1002)
┌───────────────────────┐          ┌───────────────────────┐
│ _PENDING_SIGNAL_TASKS │          │ _PENDING_SIGNAL_TASKS │
│ {candidate_A: [t1]}   │          │ {}  ← empty           │
│ _ANALYSIS_TASKS       │          │ _ANALYSIS_TASKS       │
│ {}                    │          │ {candidate_A: True} ← │ triggers final analysis
└───────────────────────┘          │   without waiting t1  │
                                   └───────────────────────┘
```

This is a **silent correctness failure**, not just a performance issue. Your analysis could run on incomplete signal data and you would never know.

Furthermore, if a worker restarts due to a memory error or deployment, every `asyncio.Task` it holds disappears. No retry, no recovery, no visibility.

### 1.2 The Event Loop Saturation Problem

Your WebSocket handler (`agent/routes.py`) does several things that compete for the same event loop:

- Proxies the live voice conversation to Gemini Live (sustained I/O over the interview duration)
- Runs an autosave loop every 5 seconds (DB write + response compatibility sync)
- Calls `extract_signals_for_segment` as an asyncio task (Gemini API call) on every question completion
- Calls `schedule_final_analysis` (another Gemini API call chain) at interview end

With 100 concurrent users, you have 100 WebSocket connections, each triggering its own asyncio task chain inside the same event loop. Even though asyncio is non-blocking, every Gemini API call awaits an I/O response. If Gemini is slow (and it will be under any external load spike), these awaits pile up. The event loop does not saturate CPU — it saturates the number of concurrent coroutines waiting for I/O, and Python's asyncio event loop has a practical ceiling well below 100 simultaneous Gemini round-trips.

### 1.3 The Synchronous Assessment Creation Trap

`POST /assessments/generate` and `POST /assessments/generate-from-linkedin-job` are currently synchronous from the caller's perspective. The chain looks like:

```
HTTP Request arrives
  → brightdata_linkedin.py scrapes LinkedIn (external HTTP, slow, unpredictable)
  → ai_analysis.py calls Gemini to generate job requirements (AI call, 2–10s)
  → assessment_registry.py writes assessments + assessment_items
  → rag/embeddings.py generates vector embeddings for assessment context (AI call)
  → assessment_access_links created
HTTP Response returned
```

Total latency: easily 15–40 seconds. This holds the HTTP connection open the entire time. Under concurrent admin usage, this blocks worker capacity.

### 1.4 The GET-Triggers-Compute Problem

In `intelligence.py`, `GET /intelligence/assessment/{id}/status` can itself call `schedule_final_analysis` if it detects the interview is complete but analysis is missing. This means a read endpoint — which admins may poll repeatedly — can silently trigger a full Gemini analysis pipeline run. Under load, multiple admin clients polling status could fan-out multiple simultaneous analysis runs for the same candidate, corrupting the `analysis` and `predictions` tables with concurrent writes.

### 1.5 Problem Summary Table

| Problem | Location | Impact at 100 Users |
|---|---|---|
| In-memory task tracking | `intelligence.py` (`_PENDING_SIGNAL_TASKS`, `_ANALYSIS_TASKS`) | Silent data corruption, orphaned tasks, no cross-worker coordination |
| Event loop saturation | `agent/routes.py`, `intelligence.py` | Cascading slowdowns, dropped WebSocket messages |
| Synchronous assessment creation | `assessment_registry.py`, `ai_analysis.py`, `rag/embeddings.py` | 15–40s HTTP holds, worker exhaustion |
| GET triggering compute | `routers/intelligence.py` → `intelligence.py` | Duplicate analysis runs, concurrent DB write conflicts |
| No task retry or persistence | Entire async pipeline | Lost work on worker restart, no observability |
| No distributed locking | `intelligence.py` | Multiple analysis runs per candidate under concurrent load |

---

## 2. The Full Technology Stack Map

Here is where each technology lands in your architecture before diving into each individually:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          CLIENT / BROWSER                                        │
│          WebSocket (interview)  ·  REST (assessment mgmt, results)               │
└──────────────────────────────────┬──────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────────┐
│                     FASTAPI APPLICATION (Uvicorn, N workers)                     │
│                                                                                  │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────────────┐ │
│  │  agent/routes.py │   │ assessment_       │   │  routers/intelligence.py     │ │
│  │  (WebSocket)     │   │ registry.py       │   │  routers/candidate.py        │ │
│  └────────┬─────────┘   └────────┬──────────┘   └──────────────────────────────┘ │
│           │ dispatch              │ dispatch                    │ read from Redis  │
└───────────┼───────────────────────┼─────────────────────────────┼─────────────────┘
            │                       │                             │
            ▼                       ▼                             ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         REDIS                                                     │
│  • Celery result backend          • Distributed locks (analysis dedup)            │
│  • In-flight task ID registry     • Session/context cache                         │
│  • Assessment definition cache    • job_requirement_profiles cache                │
│  • Rate limit counters (Gemini)   • assessment_context_embeddings cache           │
└──────────────────────────┬────────────────────────────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         RABBITMQ (Celery Broker)                                  │
│                                                                                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐  ┌────────────┐ │
│  │  signals queue   │  │  analysis queue  │  │embeddings queue│  │scraping    │ │
│  │  (high priority) │  │  (medium prio)   │  │(low priority)  │  │queue       │ │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬────────┘  └─────┬──────┘ │
│           │                     │                     │                 │         │
│  ┌────────▼─────────────────────▼─────────────────────▼─────────────────▼──────┐ │
│  │                    Dead Letter Exchange (DLX)                                │ │
│  │   Failed tasks after N retries → dlq for inspection / alerting              │ │
│  └────────────────────────────────────────────────────────────────────────────-┘ │
└──────────────────────────┬────────────────────────────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         CELERY WORKERS                                            │
│                                                                                   │
│  ┌──────────────────────────┐     ┌──────────────────────────┐                   │
│  │  Signal Workers (4–8)    │     │  Analysis Workers (2–4)  │                   │
│  │  consume: signals queue  │     │  consume: analysis queue │                   │
│  │  tasks.extract_signals   │     │  tasks.run_final_analysis│                   │
│  └──────────────────────────┘     └──────────────────────────┘                   │
│                                                                                   │
│  ┌──────────────────────────┐     ┌──────────────────────────┐                   │
│  │  Embedding Workers (2)   │     │  Scraping Workers (1–2)  │                   │
│  │  consume: embeddings     │     │  consume: scraping queue │                   │
│  │  tasks.generate_embeddings│    │  tasks.scrape_linkedin   │                   │
│  └──────────────────────────┘     └──────────────────────────┘                   │
└──────────────────────────┬────────────────────────────────────────────────────────┘
                           │  read / write
                           ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         POSTGRESQL                                                 │
│  assessment_answers · responses · response_segments · response_signals            │
│  analysis · predictions · assessment_results · job_requirement_profiles           │
│  assessments · assessment_items · assessment_context_embeddings · ...             │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. RabbitMQ

### 3.1 What It Is in This Context

RabbitMQ is the **message broker** — it is the pipe between your FastAPI application and the Celery workers. When your FastAPI code calls `some_task.apply_async(args=[...])`, it does not execute the function. It serializes the task payload (JSON) and publishes a message to RabbitMQ. A Celery worker listening on the appropriate queue picks it up, executes the function, and stores the result in Redis.

RabbitMQ is separate from Celery itself. Celery also supports using Redis as its broker, so you might ask: why RabbitMQ at all?

### 3.2 Why RabbitMQ and Not Redis-as-Broker

For your specific application, RabbitMQ is the correct broker choice for these reasons:

**Message durability.** Your signal extraction and final analysis tasks are the output of a real interview session. If a task is in-flight when the broker restarts, RabbitMQ (with `delivery_mode=2` persistent messages) survives the restart and redelivers. Redis-as-broker does not persist messages across restarts by default unless AOF or RDB is explicitly tuned for it — and even then, it is not designed for this use case.

**Dead Letter Exchange (DLX).** When a signal extraction task fails after N retries (Gemini API error, DB constraint, etc.), RabbitMQ can route it to a Dead Letter Queue automatically. This gives you visibility into failed analysis work — critical when the output is a hiring decision.

**Priority queues.** Your `signals` queue tasks (blocking the final analysis from starting) should be processed before `embeddings` queue tasks (non-blocking background work). RabbitMQ's native queue priorities handle this cleanly. Redis-as-broker simulates this less reliably.

**Consumer acknowledgment model.** RabbitMQ does not mark a message as consumed until the worker sends an `ack`. If the worker dies mid-task, the message is requeued automatically. This makes task delivery semantics exactly-once rather than at-most-once.

### 3.3 Queue Configuration

```python
# celery_config.py

from kombu import Queue, Exchange

RABBITMQ_URL = "amqp://user:password@rabbitmq:5672//"

# Exchanges
default_exchange = Exchange("default", type="direct")
dlx_exchange    = Exchange("dlx", type="direct")

# Queues — each maps to a class of work
task_queues = (
    Queue("signals",    default_exchange, routing_key="signals",
          queue_arguments={"x-dead-letter-exchange": "dlx",
                           "x-dead-letter-routing-key": "dlq",
                           "x-message-ttl": 1_800_000}),     # 30 min TTL
    Queue("analysis",   default_exchange, routing_key="analysis",
          queue_arguments={"x-dead-letter-exchange": "dlx",
                           "x-dead-letter-routing-key": "dlq"}),
    Queue("embeddings", default_exchange, routing_key="embeddings"),
    Queue("scraping",   default_exchange, routing_key="scraping"),
    Queue("dlq",        dlx_exchange,    routing_key="dlq"),  # dead letters
)

task_default_queue = "signals"
task_default_exchange = "default"
task_default_routing_key = "signals"
```

### 3.4 Why the `signals` Queue Exists Separately

Signal extraction tasks (`extract_signals_for_segment`) are on the critical path to final analysis. If they share a queue with low-priority tasks like embedding generation, a burst of assessment creations could delay signal extraction and stall the entire intelligence pipeline for active interviews. Separate queues with dedicated worker pools prevent this cross-contamination.

---

## 4. Celery

### 4.1 What It Replaces

Celery replaces the entire in-process async pipeline that currently lives in `intelligence.py`:

| Current (Broken at Scale) | Celery Replacement |
|---|---|
| `asyncio.create_task(extract_signals_for_segment(...))` | `extract_signals.apply_async(args=[seg_id, candidate_id], queue="signals")` |
| `_PENDING_SIGNAL_TASKS` dict tracking | Task IDs stored in Redis per candidate scope |
| `schedule_final_analysis` → `asyncio.gather(*tasks)` | Celery `chord`: fan-out + callback |
| `_ANALYSIS_TASKS` dict deduplication | Redis distributed lock (`SET NX EX`) |
| In-process assessment creation chain | Celery chain: `scrape_linkedin | analyze_job | generate_assessment | generate_embeddings` |

### 4.2 Task Definitions

```python
# tasks/signals.py

from celery import shared_task
from app.services.intelligence import extract_signals_for_segment_sync
import redis

r = redis.Redis.from_url(REDIS_URL)

@shared_task(
    name="tasks.extract_signals",
    bind=True,
    max_retries=3,
    default_retry_delay=10,      # seconds between retries
    acks_late=True,              # only ack after task completes
    queue="signals",
)
def extract_signals(self, segment_id: str, candidate_id: str, assessment_id: str):
    """
    Extracts intelligence signals from a single response_segment.
    Replaces the asyncio.create_task(extract_signals_for_segment(...)) pattern.
    This runs in a Celery worker, completely outside the FastAPI event loop.
    """
    try:
        result = extract_signals_for_segment_sync(
            segment_id=segment_id,
            candidate_id=candidate_id,
            assessment_id=assessment_id,
        )
        return {"segment_id": segment_id, "signals_count": result.signals_count}
    except Exception as exc:
        raise self.retry(exc=exc)


# tasks/analysis.py

from celery import shared_task, chord, group
from app.services.intelligence import run_full_analysis_chained_sync

@shared_task(
    name="tasks.run_final_analysis",
    bind=True,
    max_retries=2,
    acks_late=True,
    queue="analysis",
)
def run_final_analysis(self, signal_results: list, candidate_id: str, assessment_id: str):
    """
    Runs after ALL signal extraction tasks complete (via chord callback).
    signal_results is the list of return values from each extract_signals task.
    Replaces schedule_final_analysis + run_full_analysis_chained.
    """
    # Distributed lock: ensure only one analysis runs per candidate scope
    lock_key = f"analysis_lock:{candidate_id}"
    lock_acquired = r.set(lock_key, self.request.id, nx=True, ex=3600)

    if not lock_acquired:
        # Another worker is already running analysis for this candidate
        return {"status": "skipped", "reason": "lock_held"}

    try:
        result = run_full_analysis_chained_sync(
            candidate_id=candidate_id,
            assessment_id=assessment_id,
        )
        return {"status": "completed", "analysis_id": result.analysis_id}
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        r.delete(lock_key)
```

### 4.3 The Chord Pattern — Replacing `schedule_final_analysis`

The most important architectural change. Currently, `schedule_final_analysis` calls `asyncio.gather(*pending_signal_tasks)` to wait for all signals before running analysis. With Celery, this becomes a **chord**: a group of parallel tasks whose completion automatically triggers a callback.

```python
# In agent/routes.py — when the interview ends or the last question completes

from celery import chord, group
from tasks.signals import extract_signals
from tasks.analysis import run_final_analysis

async def on_interview_complete(candidate_id: str, assessment_id: str, segment_ids: list[str]):
    """
    Called when the WebSocket determines the interview is complete.
    Replaces the asyncio.create_task chain + schedule_final_analysis.
    """

    # Build a group of parallel signal extraction tasks (one per segment)
    signal_group = group(
        extract_signals.s(seg_id, candidate_id, assessment_id)
        for seg_id in segment_ids
    )

    # Chord: run all signals in parallel, then run final analysis with results
    pipeline = chord(signal_group)(
        run_final_analysis.s(
            candidate_id=candidate_id,
            assessment_id=assessment_id,
        )
    )

    # Store the chord ID in Redis so the status endpoint can check it
    r.set(f"pipeline:{candidate_id}", pipeline.id, ex=7200)

    # WebSocket handler returns immediately — all processing is now in workers
    return {"status": "processing", "pipeline_id": pipeline.id}
```

This single pattern eliminates:
- `_PENDING_SIGNAL_TASKS` (Celery tracks the group internally via Redis)
- `_ANALYSIS_TASKS` (the chord callback fires exactly once)
- The asyncio blocking in the WebSocket handler
- Cross-worker coordination failure

The chord's internal state is stored in Redis (your result backend). It is process-independent and survives worker restarts.

### 4.4 Assessment Creation Pipeline — Celery Chain

```python
# tasks/assessment.py

from celery import chain, shared_task

@shared_task(name="tasks.scrape_linkedin", queue="scraping", max_retries=3)
def scrape_linkedin(linkedin_url: str, owner_user_id: str):
    """Replaces brightdata_linkedin.py blocking call in the HTTP handler."""
    from app.services.brightdata_linkedin import scrape_job_sync
    return scrape_job_sync(linkedin_url)  # returns raw job data dict

@shared_task(name="tasks.analyze_job_requirements", queue="analysis", max_retries=2)
def analyze_job_requirements(raw_job_data: dict, owner_user_id: str):
    """Replaces ai_analysis.py blocking call."""
    from app.services.ai_analysis import analyze_job_sync
    job_req = analyze_job_sync(raw_job_data)
    return {"job_requirement_id": str(job_req.id)}

@shared_task(name="tasks.generate_assessment", queue="analysis", max_retries=2)
def generate_assessment(job_analysis_result: dict, owner_user_id: str):
    """Runs assessment_registry.py generation."""
    from app.services.assessment_registry import generate_from_job_sync
    assessment = generate_from_job_sync(job_analysis_result["job_requirement_id"], owner_user_id)
    return {"assessment_id": str(assessment.id)}

@shared_task(name="tasks.generate_embeddings", queue="embeddings", max_retries=3)
def generate_embeddings(assessment_result: dict):
    """Runs rag/embeddings.py — low priority, non-blocking."""
    from app.rag.embeddings import generate_context_embeddings_sync
    generate_context_embeddings_sync(assessment_result["assessment_id"])
    return {"status": "embedded"}


# In the FastAPI route — assessment_registry.py route handler:
@router.post("/assessments/generate-from-linkedin-job", status_code=202)
async def generate_from_linkedin(payload: LinkedInGenerateRequest, current_user: User = Depends(...)):
    """
    Returns 202 Accepted immediately.
    The entire scrape → analyze → generate → embed chain runs in Celery workers.
    """
    pipeline = chain(
        scrape_linkedin.s(payload.linkedin_url, str(current_user.id)),
        analyze_job_requirements.s(str(current_user.id)),
        generate_assessment.s(str(current_user.id)),
        generate_embeddings.s(),
    )
    task = pipeline.apply_async()

    # Store task ID so the admin can poll progress
    return {"status": "accepted", "task_id": task.id, "poll_url": f"/assessments/status/{task.id}"}
```

### 4.5 Status Endpoint — Removing the GET-Triggers-Compute Anti-Pattern

```python
# routers/intelligence.py

@router.get("/intelligence/assessment/{assessment_id}/status")
async def get_analysis_status(assessment_id: str, candidate_id: str = Query(...)):
    """
    BEFORE: This could trigger schedule_final_analysis directly (dangerous).
    AFTER: It only reads state. It never triggers computation.
    """
    # Check Celery task state via Redis result backend
    pipeline_id = r.get(f"pipeline:{candidate_id}")
    if pipeline_id:
        result = AsyncResult(pipeline_id.decode())
        if result.state == "SUCCESS":
            return {"status": "complete"}
        elif result.state == "FAILURE":
            return {"status": "failed", "error": str(result.result)}
        else:
            return {"status": "processing", "state": result.state}

    # No pipeline tracked — check if analysis exists in DB
    analysis = await get_analysis_by_candidate(candidate_id)
    if analysis:
        return {"status": "complete"}

    # Analysis is missing and no pipeline is running
    # Do NOT trigger analysis here. Return a distinct status.
    return {"status": "pending", "action": "use POST /intelligence/assessment/{id}/rerun to trigger"}
```

### 4.6 Rerun Endpoint — Already a Natural Celery Task

```python
@router.post("/intelligence/assessment/{assessment_id}/rerun")
async def rerun_analysis(assessment_id: str, candidate_id: str = Query(...)):
    """
    BEFORE: Called intelligence.py functions directly in-process.
    AFTER: Dispatches to the analysis worker. Returns immediately.
    """
    # Check lock before dispatching
    lock_key = f"analysis_lock:{candidate_id}"
    if r.exists(lock_key):
        return {"status": "already_running"}

    # Fetch existing segment IDs from DB
    segment_ids = await get_segment_ids_for_candidate(candidate_id)

    pipeline = chord(
        group(extract_signals.s(seg_id, candidate_id, assessment_id) for seg_id in segment_ids)
    )(run_final_analysis.s(candidate_id=candidate_id, assessment_id=assessment_id))

    r.set(f"pipeline:{candidate_id}", pipeline.id, ex=7200)
    return {"status": "dispatched", "pipeline_id": pipeline.id}
```

---

## 5. Redis

### 5.1 Dual Role: Result Backend and Cache Layer

Redis serves two distinct purposes in this architecture. Conflating them is a common mistake — they should share the same Redis instance but use different key namespaces and TTLs.

```
Redis Key Namespace Map
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Role 1: Celery Result Backend
  celery-task-meta-{task_id}         → task state + result JSON
  (managed by Celery automatically)

Role 2: Task Tracking (replacing _PENDING_SIGNAL_TASKS)
  pipeline:{candidate_id}            → chord task ID   TTL: 2h
  signal_task:{segment_id}           → task ID         TTL: 1h

Role 3: Distributed Locking (replacing _ANALYSIS_TASKS)
  analysis_lock:{candidate_id}       → running task ID TTL: 1h

Role 4: Read Cache
  cache:assessment:{id}:definition   → assessment JSON TTL: 24h
  cache:assessment:{id}:items        → items JSON      TTL: 24h
  cache:candidate:{id}:context       → access context  TTL: 30min
  cache:job_req_profile:{id}         → profile JSON    TTL: 6h
  cache:embeddings:{assessment_id}   → embedding meta  TTL: 12h

Role 5: Rate Limiting
  ratelimit:gemini:{worker_id}       → request count   TTL: 1min
```

### 5.2 Replacing `_PENDING_SIGNAL_TASKS` and `_ANALYSIS_TASKS`

```python
# app/services/task_registry.py
# This module REPLACES the in-memory dicts in intelligence.py

import redis
import json
from typing import Optional

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

class TaskRegistry:

    def register_signal_task(self, segment_id: str, candidate_id: str, task_id: str):
        """Register a signal extraction task. Replaces _PENDING_SIGNAL_TASKS dict."""
        key = f"signal_task:{segment_id}"
        r.set(key, task_id, ex=3600)
        # Also append to candidate's task list for bulk lookup
        list_key = f"signal_tasks_list:{candidate_id}"
        r.rpush(list_key, task_id)
        r.expire(list_key, 3600)

    def get_signal_task_ids(self, candidate_id: str) -> list[str]:
        """Get all signal task IDs for a candidate."""
        return r.lrange(f"signal_tasks_list:{candidate_id}", 0, -1)

    def acquire_analysis_lock(self, candidate_id: str, task_id: str) -> bool:
        """Distributed lock replacing _ANALYSIS_TASKS. Returns True if lock acquired."""
        lock_key = f"analysis_lock:{candidate_id}"
        return r.set(lock_key, task_id, nx=True, ex=3600) is not None

    def release_analysis_lock(self, candidate_id: str):
        r.delete(f"analysis_lock:{candidate_id}")

    def is_analysis_running(self, candidate_id: str) -> bool:
        return r.exists(f"analysis_lock:{candidate_id}") == 1
```

### 5.3 The Read Cache — Eliminating Repeated DB Queries Per WebSocket Message

Every WebSocket connection in `agent/routes.py` calls:
- `get_candidate_access_context` → reads `assessment_candidates`, `assessment_access_links`
- `get_assessment_definition` → reads `assessments`, `job_requirements`
- `get_assessment_item_payloads` → reads `assessment_items`

These are called once at connection start, but the data is static for the entire interview. With 100 concurrent interviews, that is 100 identical DB queries for the same assessment definition rows. Redis caching eliminates this:

```python
# app/services/cached_reads.py

import redis, json
from app.services import assessment_registry

r = redis.Redis.from_url(REDIS_URL)

async def get_assessment_definition_cached(assessment_id: str) -> dict:
    cache_key = f"cache:assessment:{assessment_id}:definition"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)

    # Cache miss: hit DB and store
    definition = await assessment_registry.get_assessment_definition(assessment_id)
    r.set(cache_key, json.dumps(definition), ex=86400)  # 24h TTL
    return definition

async def get_assessment_items_cached(assessment_id: str) -> list:
    cache_key = f"cache:assessment:{assessment_id}:items"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)

    items = await assessment_registry.get_assessment_item_payloads(assessment_id)
    r.set(cache_key, json.dumps(items), ex=86400)
    return items

async def get_candidate_context_cached(candidate_id: str) -> dict:
    """30-min TTL — candidate context is semi-mutable (link revocation)."""
    cache_key = f"cache:candidate:{candidate_id}:context"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)

    context = await assessment_candidates.get_candidate_access_context(candidate_id)
    r.set(cache_key, json.dumps(context), ex=1800)  # 30 min
    return context

async def get_job_requirement_profile_cached(job_req_id: str) -> dict:
    """Cached for 6h — profiles are computed once and rarely change."""
    cache_key = f"cache:job_req_profile:{job_req_id}"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)

    profile = await intelligence.ensure_job_requirement_profile(job_req_id)
    r.set(cache_key, json.dumps(profile), ex=21600)  # 6h
    return profile
```

**Cache invalidation rules:**
- `assessment` caches → invalidate when `POST /assessments/generate` completes
- `candidate` context → invalidate on `POST /assessments/{id}/invite-link/revoke`
- `job_req_profile` → invalidate on `POST /intelligence/assessment/{id}/rerun`

### 5.4 Gemini API Rate Limiting

With 100 concurrent interviews all triggering Gemini calls, you risk hitting Gemini's RPM (requests per minute) limits. Redis provides a sliding window rate limiter shared across all workers:

```python
# app/services/rate_limiter.py

import redis, time

r = redis.Redis.from_url(REDIS_URL)

def check_gemini_rate_limit(limit: int = 60, window_seconds: int = 60) -> bool:
    """
    Returns True if the request is allowed, False if rate limit exceeded.
    Uses a sliding window counter shared across ALL Celery workers and FastAPI workers.
    """
    key = f"ratelimit:gemini:{int(time.time() // window_seconds)}"
    count = r.incr(key)
    if count == 1:
        r.expire(key, window_seconds * 2)  # Overlap for window boundary safety
    return count <= limit
```

This is critical: without a shared rate limiter, each Celery worker enforces its own limit independently, meaning 4 workers × 60 RPM = 240 RPM against an API that may cap at 60 globally.

---

## 6. Implementation Guide

### 6.1 Project Structure Changes

```
app/
├── celery_app.py              ← NEW: Celery application instance
├── celery_config.py           ← NEW: Broker, backend, queue config
├── tasks/
│   ├── __init__.py
│   ├── signals.py             ← NEW: extract_signals task
│   ├── analysis.py            ← NEW: run_final_analysis task
│   ├── assessment.py          ← NEW: scrape_linkedin, generate_assessment, generate_embeddings
│   └── legacy.py              ← NEW: wrap POST /analysis/run in a task
├── services/
│   ├── task_registry.py       ← NEW: Redis-backed task tracking
│   ├── cached_reads.py        ← NEW: Redis cache wrappers
│   ├── rate_limiter.py        ← NEW: Gemini rate limiter
│   ├── intelligence.py        ← MODIFIED: remove asyncio task tracking dicts
│   ├── assessment_registry.py ← MODIFIED: dispatch Celery tasks instead of blocking
│   └── ...                    ← (other services unchanged)
└── routers/
    ├── intelligence.py        ← MODIFIED: remove GET-triggers-compute
    └── ...
```

### 6.2 Celery Application Setup

```python
# app/celery_app.py

from celery import Celery

def create_celery_app() -> Celery:
    app = Celery("leadership_assessment")
    app.config_from_object("app.celery_config")
    app.autodiscover_tasks(["app.tasks"])
    return app

celery_app = create_celery_app()
```

```python
# app/celery_config.py

broker_url = "amqp://user:password@rabbitmq:5672//"
result_backend = "redis://redis:6379/0"

# Serialization
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]

# Reliability settings
task_acks_late = True                  # Ack only after task finishes
worker_prefetch_multiplier = 1         # One task at a time per worker slot
task_reject_on_worker_lost = True      # Requeue if worker dies mid-task

# Result TTL
result_expires = 86400                 # 24 hours

# Routing
task_routes = {
    "tasks.extract_signals":          {"queue": "signals"},
    "tasks.run_final_analysis":       {"queue": "analysis"},
    "tasks.generate_embeddings":      {"queue": "embeddings"},
    "tasks.scrape_linkedin":          {"queue": "scraping"},
    "tasks.analyze_job_requirements": {"queue": "analysis"},
    "tasks.generate_assessment":      {"queue": "analysis"},
}

# Retry policy
task_max_retries = 3
task_default_retry_delay = 15
```

### 6.3 Docker Compose Addition

```yaml
# docker-compose.yml additions

services:
  rabbitmq:
    image: rabbitmq:3.12-management
    ports:
      - "5672:5672"
      - "15672:15672"   # Management UI
    environment:
      RABBITMQ_DEFAULT_USER: user
      RABBITMQ_DEFAULT_PASS: password
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq

  redis:
    image: redis:7.2-alpine
    ports:
      - "6379:6379"
    command: redis-server --save 60 1 --loglevel warning
    volumes:
      - redis_data:/data

  celery-signals:
    build: .
    command: celery -A app.celery_app worker -Q signals -c 8 --loglevel=info
    depends_on: [rabbitmq, redis, db]
    environment: *app-env

  celery-analysis:
    build: .
    command: celery -A app.celery_app worker -Q analysis -c 4 --loglevel=info
    depends_on: [rabbitmq, redis, db]
    environment: *app-env

  celery-background:
    build: .
    command: celery -A app.celery_app worker -Q embeddings,scraping -c 2 --loglevel=info
    depends_on: [rabbitmq, redis, db]
    environment: *app-env

  celery-flower:
    image: mher/flower:2.0
    command: celery flower --broker=amqp://user:password@rabbitmq:5672//
    ports:
      - "5555:5555"     # Monitoring UI
    depends_on: [rabbitmq]

volumes:
  rabbitmq_data:
  redis_data:
```

### 6.4 Modifications to `intelligence.py` — Exact Change Points

```python
# BEFORE (intelligence.py)
_PENDING_SIGNAL_TASKS: dict[str, list[asyncio.Task]] = {}
_ANALYSIS_TASKS: dict[str, asyncio.Task] = {}

async def register_signal_task(candidate_id: str, task: asyncio.Task):
    _PENDING_SIGNAL_TASKS.setdefault(candidate_id, []).append(task)

async def schedule_final_analysis(candidate_id: str, assessment_id: str):
    pending = _PENDING_SIGNAL_TASKS.get(candidate_id, [])
    if pending:
        await asyncio.gather(*pending)
    await run_full_analysis_chained(candidate_id, assessment_id)


# AFTER (intelligence.py) — these functions become thin dispatchers
from app.services.task_registry import TaskRegistry
from app.tasks.signals import extract_signals
from app.tasks.analysis import run_final_analysis
from celery import chord, group

registry = TaskRegistry()

def dispatch_signal_extraction(segment_id: str, candidate_id: str, assessment_id: str) -> str:
    """Dispatch to Celery. Returns task ID. Non-blocking."""
    task = extract_signals.apply_async(
        args=[segment_id, candidate_id, assessment_id],
        queue="signals",
    )
    registry.register_signal_task(segment_id, candidate_id, task.id)
    return task.id

def dispatch_final_analysis(candidate_id: str, assessment_id: str, segment_ids: list[str]) -> str:
    """Dispatch chord to Celery. Returns pipeline ID. Non-blocking."""
    pipeline = chord(
        group(extract_signals.s(sid, candidate_id, assessment_id) for sid in segment_ids)
    )(run_final_analysis.s(candidate_id=candidate_id, assessment_id=assessment_id))
    return pipeline.id
```

### 6.5 Modifications to `agent/routes.py` — WebSocket Handler

```python
# BEFORE — agent/routes.py (simplified)
async def on_question_complete(segment_id, candidate_id, assessment_id):
    task = asyncio.create_task(extract_signals_for_segment(segment_id, ...))
    await register_signal_task(candidate_id, task)

async def on_interview_complete(candidate_id, assessment_id):
    await schedule_final_analysis(candidate_id, assessment_id)
    # WebSocket hangs here waiting for analysis

# AFTER — agent/routes.py
async def on_question_complete(segment_id, candidate_id, assessment_id):
    # Fire-and-forget — returns immediately, Celery handles it
    task_id = dispatch_signal_extraction(segment_id, candidate_id, assessment_id)
    logger.info(f"Signal extraction dispatched: {task_id}")

async def on_interview_complete(candidate_id, assessment_id, segment_ids):
    # Non-blocking — chord is dispatched to RabbitMQ, WebSocket can close immediately
    pipeline_id = dispatch_final_analysis(candidate_id, assessment_id, segment_ids)
    r.set(f"pipeline:{candidate_id}", pipeline_id, ex=7200)
    # WebSocket closes here — analysis continues in workers
```

---

## 7. Migration Details

These are the specific technical risks and decisions you must address before migrating. Ignoring any of these will cause silent failures.

### 7.1 Async vs. Sync Task Functions

Celery workers run in a synchronous Python context by default. Your existing service code is written with `async def` and uses `await`. You have three options:

**Option A (Recommended): Create sync wrappers.** Keep async code in FastAPI, write sync wrappers for Celery task bodies using `asyncio.run()`:

```python
# tasks/signals.py
@shared_task(...)
def extract_signals(segment_id, candidate_id, assessment_id):
    import asyncio
    return asyncio.run(_extract_signals_async(segment_id, candidate_id, assessment_id))

async def _extract_signals_async(segment_id, candidate_id, assessment_id):
    # Your existing async logic here
    ...
```

**Option B:** Use `celery[gevent]` or `celery[eventlet]` for async-compatible workers. More complex to configure, not recommended unless your tasks are heavily I/O bound with many awaits.

**Option C:** Rewrite task bodies to be synchronous (use `psycopg2` directly instead of async SQLAlchemy in worker context). Clean but requires dual DB layer.

### 7.2 SQLAlchemy Session Management in Workers

Your FastAPI routes use dependency injection for DB sessions (e.g., `db: AsyncSession = Depends(get_db)`). Celery tasks do not have FastAPI's dependency injection. You must create sessions manually in each task:

```python
# tasks/base.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Synchronous engine for Celery workers (separate from FastAPI's async engine)
sync_engine = create_engine(DATABASE_URL.replace("+asyncpg", "+psycopg2"))
SyncSessionLocal = sessionmaker(bind=sync_engine)

def get_sync_db():
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**Critical:** Do not share the same `AsyncEngine` between FastAPI and Celery workers. Celery workers do not have an event loop at import time.

### 7.3 Chord Failure Handling

When using `chord(signal_group)(run_final_analysis.s(...))`, if any task in the group raises an exception after exhausting retries, the chord callback (`run_final_analysis`) **may not be called** depending on your Celery version and `CELERY_CHORD_PROPAGATES` setting. Always implement a safety fallback:

```python
# tasks/analysis.py — chord callback
@shared_task(name="tasks.run_final_analysis")
def run_final_analysis(signal_results, candidate_id, assessment_id):
    # signal_results may contain exceptions if chord_propagates=False
    failed = [r for r in signal_results if isinstance(r, Exception)]
    if failed:
        logger.warning(f"chord had {len(failed)} failed signal tasks, proceeding with partial data")
    # Continue with analysis using whatever signals were saved to DB
    ...
```

Also configure: `CELERY_CHORD_PROPAGATES = False` to ensure the callback always fires even if individual signal tasks fail.

### 7.4 Idempotency — Preventing Double Analysis Writes

If a rerun is triggered while a chord is still completing, you could write two `analysis` rows. The Redis lock handles this in the happy path, but the DB must also be safe:

```sql
-- Add a unique constraint to prevent duplicate analysis rows
ALTER TABLE analysis
  ADD CONSTRAINT uq_analysis_candidate UNIQUE (assessment_id, candidate_id, created_at::date);
```

Or use `INSERT ... ON CONFLICT DO NOTHING` in your analysis persistence layer.

### 7.5 The Legacy `/analysis/run` Path

Your codebase has two analysis producers (`intelligence.py` and `as_analysis/routes/analysis.py`). The legacy path must also be wrapped in a Celery task, otherwise it bypasses the distributed locking:

```python
# tasks/legacy.py
@shared_task(name="tasks.legacy_run_analysis", queue="analysis")
def legacy_run_analysis(assessment_id: str, candidate_id: str):
    """Wraps the POST /analysis/run path. Ensures it goes through Redis lock."""
    if not registry.acquire_analysis_lock(candidate_id, legacy_run_analysis.request.id):
        return {"status": "skipped"}
    try:
        # existing legacy analysis logic here
        ...
    finally:
        registry.release_analysis_lock(candidate_id)
```

### 7.6 WebSocket Autosave — No Change Required

The autosave loop every 5 seconds (`save_assessment_answer` → `assessment_answers` + `responses`) does **not** need Celery. It is already a simple async DB write that does not involve Gemini or heavy computation. Leave it in the WebSocket handler as-is. Offloading simple DB writes to Celery would add latency (RabbitMQ round-trip) for no benefit.

### 7.7 Monitoring — Do Not Skip This

Celery without monitoring is flying blind. Flower provides a real-time task dashboard:

- Active tasks per queue
- Task failure rates and error details
- Worker health
- Task timing (identify slow Gemini calls)

Access at `http://your-host:5555`. Configure alerts for DLQ message count — if the dead letter queue grows, analysis is failing silently.

### 7.8 Environment Variables Required

```bash
# Add to .env
CELERY_BROKER_URL=amqp://user:password@rabbitmq:5672//
CELERY_RESULT_BACKEND=redis://redis:6379/0
REDIS_URL=redis://redis:6379/0

# Gemini rate limit (tune to your API tier)
GEMINI_RPM_LIMIT=60

# Worker concurrency (tune per machine)
CELERY_SIGNALS_CONCURRENCY=8
CELERY_ANALYSIS_CONCURRENCY=4
```

---

## 8. Updated Architecture Diagrams

### 8.1 Full End-to-End Flow With New Stack

```mermaid
flowchart TD
    subgraph Client
        A1[Admin Browser]
        A2[Candidate Browser]
    end

    subgraph FastAPI Workers
        F1[POST /assessments/generate]
        F2[WebSocket /agent/ws]
        F3[GET /intelligence/status]
        F4[POST /intelligence/rerun]
    end

    subgraph Redis
        R1[Celery Result Backend]
        R2[Distributed Lock Store]
        R3[Task ID Registry]
        R4[Read Cache]
        R5[Rate Limiter]
    end

    subgraph RabbitMQ
        Q1[signals queue]
        Q2[analysis queue]
        Q3[embeddings queue]
        Q4[scraping queue]
        Q5[dead letter queue]
    end

    subgraph Celery Workers
        W1[Signal Workers x8]
        W2[Analysis Workers x4]
        W3[Background Workers x2]
    end

    subgraph PostgreSQL
        DB[(Database)]
    end

    A1 --> F1
    A2 --> F2
    A1 --> F3
    A1 --> F4

    F1 -->|202 Accepted| A1
    F1 -->|dispatch chain| Q4
    F1 -->|dispatch| Q3

    F2 -->|cache lookup| R4
    F2 -->|dispatch signal task| Q1
    F2 -->|dispatch chord callback| Q2
    F2 -->|register task ID| R3

    F3 -->|read task state| R1
    F3 -->|check lock| R2
    F3 -.->|never triggers compute| F3

    F4 -->|set lock| R2
    F4 -->|dispatch chord| Q1
    F4 -->|dispatch| Q2

    Q4 --> W3
    Q3 --> W3
    Q1 --> W1
    Q2 --> W2

    W1 -->|rate check| R5
    W1 -->|Gemini API call| GeminiLive[(Gemini)]
    W1 -->|write response_signals| DB
    W1 -->|chord progress| R1

    W2 -->|acquire lock| R2
    W2 -->|Gemini API call| GeminiLive
    W2 -->|write analysis + predictions + results| DB
    W2 -->|release lock| R2

    W3 -->|scrape LinkedIn| LinkedIn[(LinkedIn)]
    W3 -->|generate embeddings| DB
    W3 -->|invalidate cache| R4

    Q1 -->|on failure after retries| Q5
    Q2 -->|on failure after retries| Q5
```

### 8.2 Signal Extraction → Final Analysis Chord Detail

```mermaid
sequenceDiagram
    participant WS as WebSocket Handler
    participant RMQ as RabbitMQ
    participant SW as Signal Workers (x8)
    participant Redis
    participant AW as Analysis Worker
    participant PG as PostgreSQL

    WS->>RMQ: chord(group(extract_signals × N))(run_final_analysis)
    WS->>Redis: SET pipeline:{candidate_id} = chord_id
    WS-->>WS: return immediately — WebSocket can close

    par Parallel signal extraction
        RMQ->>SW: extract_signals(seg_1)
        SW->>PG: write response_signals
        SW->>Redis: chord progress update
    and
        RMQ->>SW: extract_signals(seg_2)
        SW->>PG: write response_signals
        SW->>Redis: chord progress update
    and
        RMQ->>SW: extract_signals(seg_N)
        SW->>PG: write response_signals
        SW->>Redis: chord progress update
    end

    Redis->>RMQ: all N tasks complete → dispatch callback
    RMQ->>AW: run_final_analysis([results_list])
    AW->>Redis: SET analysis_lock:{candidate_id} NX
    AW->>PG: read response_signals, job_req_profiles
    AW->>PG: write analysis, predictions, assessment_results
    AW->>Redis: DEL analysis_lock:{candidate_id}
    AW->>Redis: SET pipeline status = SUCCESS
```

### 8.3 In-Process Task Tracking vs. Redis+Celery

```mermaid
flowchart LR
    subgraph BEFORE — In-Process, Broken at Scale
        direction TB
        W1P[Worker PID 1001]
        W2P[Worker PID 1002]
        D1[_PENDING_SIGNAL_TASKS dict]
        D2[_ANALYSIS_TASKS dict]
        W1P --- D1
        W1P --- D2
        W2P --- D1B[_PENDING_SIGNAL_TASKS dict COPY]
        W2P --- D2B[_ANALYSIS_TASKS dict COPY]
        style D1B fill:#ffcccc
        style D2B fill:#ffcccc
        style D1 fill:#ffcccc
        style D2 fill:#ffcccc
    end

    subgraph AFTER — Redis-Backed, Cross-Worker
        direction TB
        W1A[Worker PID 1001]
        W2A[Worker PID 1002]
        RC[Redis — Shared State]
        W1A -->|read/write| RC
        W2A -->|read/write| RC
        style RC fill:#ccffcc
    end
```

### 8.4 Assessment Creation: Sync vs. Async Chain

```mermaid
sequenceDiagram
    participant Admin
    participant FastAPI
    participant RMQ as RabbitMQ
    participant SW as Scraping Worker
    participant AW as Analysis Worker
    participant BW as Embedding Worker
    participant PG as PostgreSQL

    Note over Admin,FastAPI: BEFORE — Admin waits 15–40s
    Admin->>FastAPI: POST /generate-from-linkedin
    FastAPI->>FastAPI: scrape LinkedIn (5–15s)
    FastAPI->>FastAPI: Gemini job analysis (3–8s)
    FastAPI->>FastAPI: generate embeddings (2–5s)
    FastAPI-->>Admin: 200 OK (after 15–40s)

    Note over Admin,FastAPI: AFTER — Admin gets 202 in <100ms
    Admin->>FastAPI: POST /generate-from-linkedin
    FastAPI->>RMQ: dispatch chain(scrape | analyze | generate | embed)
    FastAPI-->>Admin: 202 Accepted {task_id}
    RMQ->>SW: scrape_linkedin task
    SW->>PG: -
    SW->>RMQ: result → triggers analyze_job
    RMQ->>AW: analyze_job_requirements
    AW->>PG: write job_requirements, assessments, assessment_items
    AW->>RMQ: result → triggers generate_embeddings
    RMQ->>BW: generate_embeddings
    BW->>PG: write assessment_context_embeddings
    Admin->>FastAPI: GET /assessments/status/{task_id}
    FastAPI-->>Admin: {status: "complete"}
```

---

## 9. Verdict — Is Any of Them Unnecessary?

### Short Answer

All three are necessary for your application. However, RabbitMQ and Redis have some overlap as Celery broker options, and this is worth addressing directly.

### Could You Use Redis as the Celery Broker Instead of RabbitMQ?

Technically yes. You can run Celery with Redis as both broker and result backend, eliminating RabbitMQ entirely. For many applications this is the right simplification. For yours, it is not, because:

**Your DLQ need is real.** Signal extraction failures mean an interview's intelligence pipeline silently stalls. The hiring manager will never see analysis results. You need the Dead Letter Queue so you can inspect, alert, and reprocess failed tasks. RabbitMQ's DLX is native and robust. Emulating this with Redis requires custom logic.

**Your chord relies on broker reliability.** Celery chords use the broker to track group completion state. Under Redis-as-broker with high chord fan-out (many segments per interview, many concurrent interviews), you can hit Redis memory pressure where broker data competes with cache data and result backend data. Separating broker (RabbitMQ) from cache/results (Redis) gives each workload its own resource envelope.

**Priority routing matters.** Signal extraction must not be starved by embedding generation tasks. RabbitMQ's priority queues and separate exchanges give you clean routing guarantees. Redis LPUSH/BRPOP does not give you priority without additional custom logic.

**Conclusion on RabbitMQ:** Keep it. The operational overhead (one additional container) is justified by the message durability, DLQ, and routing isolation it provides for a platform where task failures have direct business consequences (lost hiring analysis).

### Is Redis Overkill?

No. Redis is not optional here even if you kept the in-process asyncio queue. The read cache alone (assessment definitions, items, candidate context) provides significant DB relief that Postgres cannot provide at the same latency for 100 concurrent WebSocket connections. The distributed lock is a correctness requirement, not a performance optimization.

### Final Technology Justification Matrix

| Technology | Role | Without It, At 100 Users |
|---|---|---|
| **Celery** | Offloads all AI processing from FastAPI event loop | Event loop saturation, WebSocket delays, no task retries |
| **RabbitMQ** | Reliable broker with DLQ, priority queues, message persistence | Lost tasks on restart, no dead letter visibility, chord instability |
| **Redis** | Result backend + distributed lock + read cache + rate limiter | Cross-worker coordination failure, DB overload, Gemini quota errors |

All three are earning their place. This is not over-engineering — it is the minimum viable distributed architecture for a platform doing real-time AI-powered voice interviews at any meaningful concurrency.

---

*Document produced through full analysis of `application_visualization.md` and the codebase modules it references. Implementation examples are written to match the FastAPI + PostgreSQL + Gemini Live technology stack already in place.*