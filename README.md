# Leadership Assessment

## Database migrations

Migrations live in `app/db/migrations/versions/`. Run from the **project root**:

```bash
# Apply all migrations
alembic -c alembic.ini upgrade head

# Create a new migration (after changing models)
alembic -c alembic.ini revision --autogenerate -m "description"

# Roll back one revision
alembic -c alembic.ini downgrade -1

# Show current revision
alembic -c alembic.ini current
```

Uses `DATABASE_URL` from `app/.env` when set.

---

## RAG (embeddings for the conversation agent)

Requirements and assessment questions are stored as embeddings so the agent can retrieve relevant context during the interview.

### Where it’s used

- **Indexing**: After an assessment is generated, `index_assessment` is called in **`app/as_blueprinting/routes/assessment.py`** (in the `POST /assessments/generate` flow). That embeds the job requirements and all 10 question types and writes them to `assessment_context_embeddings`.
- **Retrieval**: The conversation agent should call **`get_context_for_agent(db, query_text, assessment_id=..., limit=15)`** (from `app/rag/embeddings.py`) with the current user message (or last few messages) to get relevant requirements and questions. Until the agent is built, you can use the **`POST /assessments/context`** endpoint to test retrieval.

### How to test

1. **Apply migrations** (includes the embeddings table and pgvector):
   ```bash
   alembic -c alembic.ini upgrade head
   ```

2. **Create an assessment** (this also runs indexing):
   ```bash
   curl -X POST http://localhost:8000/assessments/generate \
     -H "Content-Type: application/json" \
     -d '{"job_requirements_id": 1, "user_id": 1}'
   ```
   Use a real `job_requirements_id` and `user_id` from your DB.

3. **Test retrieval via API**:
   ```bash
   curl -X POST http://localhost:8000/assessments/context \
     -H "Content-Type: application/json" \
     -d '{"assessment_id": 1, "query": "How do you handle conflict?", "limit": 5}'
   ```
   Replace `1` with the assessment id returned in step 2.

4. **Test with the script** (from project root; needs `OPENAI_API_KEY` and `DATABASE_URL`):
   ```bash
   PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1
   PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1 --query "Tell me about leadership"
   PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1 --reindex
   ```
