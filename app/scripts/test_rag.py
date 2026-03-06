"""
Test script for RAG: index an assessment and retrieve context.

Usage (from project root, with .env and DB set up):
  PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1
  PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1 --reindex
  PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1 --query "How do you handle conflict?"

Requires:
  - DATABASE_URL in app/.env (or default postgres)
  - OPENAI_API_KEY in environment (for indexing and retrieval)
  - An existing assessment with id = --assessment-id (create one via POST /assessments/generate first)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure app is importable when run as script from project root
if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.db import SessionLocal
from app.db.models import Assessments, JobRequirements
from app.rag.embeddings import get_context_for_agent, index_assessment


def main() -> None:
    parser = argparse.ArgumentParser(description="Test RAG indexing and retrieval")
    parser.add_argument("--assessment-id", type=int, required=True, help="Assessment ID to use")
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Re-run index_assessment for this assessment (adds more rows if run multiple times)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="Tell me about leadership and how you approach difficult decisions.",
        help="Query text for retrieval (default: sample leadership query)",
    )
    parser.add_argument("--limit", type=int, default=5, help="Max context chunks to return (default 5)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        assessment = db.get(Assessments, args.assessment_id)
        if not assessment:
            print(f"Assessment id={args.assessment_id} not found.")
            sys.exit(1)
        job = db.get(JobRequirements, assessment.job_requirements_id)
        if not job:
            print(f"Job requirements id={assessment.job_requirements_id} not found.")
            sys.exit(1)

        if args.reindex:
            n = index_assessment(db, assessment, job)
            print(f"Indexed {n} chunks for assessment_id={args.assessment_id}.")
        else:
            print("Skipping index (use --reindex to index).")

        print(f"\nRetrieving context for query: {args.query!r}\n")
        chunks = get_context_for_agent(
            db,
            args.query,
            assessment_id=args.assessment_id,
            limit=args.limit,
        )
        if not chunks:
            print("No context returned. Run with --reindex first, or check that embeddings exist.")
        else:
            for i, c in enumerate(chunks, 1):
                print(f"--- Chunk {i} [{c['content_type']}] ---")
                print(c["content"][:300] + ("..." if len(c["content"]) > 300 else ""))
                print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
