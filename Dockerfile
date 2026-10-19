# Jenius: API + dashboard in one tiny image. Runtime is the Python standard library only.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY jenius/ jenius/
COPY data/ data/
COPY web/ web/

RUN useradd --create-home --uid 10001 app \
    && python -m jenius build \
    && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=2)"

CMD ["python", "-m", "jenius", "serve"]
