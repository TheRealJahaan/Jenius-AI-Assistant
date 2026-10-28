FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JENIUS_ENV=production \
    PORT=8000

WORKDIR /app
RUN useradd --create-home --uid 10001 jenius

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY jenius ./jenius
COPY web ./web
COPY scripts/start.sh ./scripts/start.sh
# The public demo dataset (the FreightTiger case study).
COPY ["AI Intern Case Study/shipment_records.csv", "AI Intern Case Study/context_notes.csv", "./AI Intern Case Study/"]

USER jenius
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz', timeout=4)"
CMD ["sh", "scripts/start.sh"]
