# Dhaka Transit — Production Container (FastAPI + GTFS + Frontend)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    PORT=8000

WORKDIR /app

# Install Python dependencies first for better layer caching
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy application code, database schemas, curated/GTFS data, reports, and frontend
COPY backend /app/backend
COPY database /app/database
COPY data/curated /app/data/curated
COPY data/gtfs /app/data/gtfs
COPY data/reports /app/data/reports
COPY frontend /app/frontend

# Build SQLite database from the validated GTFS feed at image build time
RUN python database/load_gtfs.py --feed data/gtfs/dhaka --db /app/dhaka_transit.sqlite

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
