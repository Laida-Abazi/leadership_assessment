FROM python:3.13-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

COPY alembic.ini ./
COPY app/ ./app/

EXPOSE 8000

CMD ["sh", "-c", \
     "alembic -c alembic.ini upgrade head && \
      uvicorn app.run:app --host 0.0.0.0 --port ${PORT:-8000}"]
